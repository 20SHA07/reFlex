"""OpenRouter worker agents. Return proposals; never mutate project files.

The host supplies scoped, controlled reads and routes proposals to its referee.
ReFlexAgents is independent of Slack and browser UI types.
See AI_AGENTS.md for integration. Python 3.10+; no additional dependencies.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class AgentError(RuntimeError):
    """Safe to show as a plain-text Slack error; no provider body or credentials."""


def _json(raw: str) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError):
        raise AgentError("Invalid JSON received; no action was submitted.") from None


def _text(value: Any, label: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AgentError(f"Invalid or oversized {label}; no action was submitted.")
    return value


def _path(value: Any) -> str:
    value = _text(value, "workspace path", 240)
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute() or any(p in {"", ".", ".."} for p in parts)
            or "\\" in value or ":" in value or any(ord(c) < 32 for c in value)):
        raise AgentError("Expected a relative workspace path; no action was submitted.")
    # Lexical check only. The backend/executor MUST enforce real-path containment,
    # symlink policy, file authorization, versions and race-safe mutations.
    return value


class ChatClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...


class ControlledBackend(Protocol):
    """All methods must be read-only, workspace-scoped and authorized by the host."""
    def list_files(self) -> list[str]: ...
    def read_file(self, path: str) -> str: ...
    def get_task_status(self, task_id: str) -> dict: ...


class OpenRouterClient:
    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, model: str, *, timeout: float = 30,
                 max_tokens: int = 3000):
        self.api_key = _text(api_key, "OpenRouter API key", 1000).strip()
        if any(ord(char) < 33 or ord(char) > 126 or char in "\"'" for char in self.api_key):
            raise AgentError("Re-enter only the OpenRouter API key, without quotes or whitespace.")
        self.model = _text(model, "OpenRouter model", 200).strip()
        if not 0 < timeout <= 120 or not 1 <= max_tokens <= 8000:
            raise AgentError("Use timeout 1–120 seconds and max_tokens 1–8000.")
        self.timeout, self.max_tokens = timeout, max_tokens

    @classmethod
    def from_env(cls) -> OpenRouterClient:
        key, model = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENROUTER_MODEL")
        if not key or not model:
            raise AgentError("Set OPENROUTER_API_KEY and OPENROUTER_MODEL before running agents.")
        return cls(key, model)

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": self.max_tokens, "stream": False}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        request = Request(self.URL, data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except HTTPError as exc:
            hints = {400: "The request was rejected. Run diagnose_openrouter.py for details.",
                     401: "Check the OpenRouter key.", 402: "Check OpenRouter credits.",
                     429: "Rate limited; retry the task later."}
            hint = hints.get(exc.code, "Check model availability and retry the task.")
            raise AgentError(f"OpenRouter HTTP {exc.code}. {hint} No action was submitted.") from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise AgentError("OpenRouter connection failed or timed out. No action was submitted.") from None
        if len(raw) > 1_000_000:
            raise AgentError("OpenRouter response exceeded the size limit.")
        try:
            data = _json(raw.decode("utf-8"))
        except UnicodeError:
            raise AgentError("OpenRouter returned invalid text.") from None
        # OpenRouter can return an error in a successful HTTP response.
        if not isinstance(data, dict) or data.get("error") is not None:
            raise AgentError("OpenRouter returned a provider error. No action was submitted.")
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise AgentError("OpenRouter returned no usable completion.")
        choice = choices[0]
        if choice.get("error") is not None or choice.get("finish_reason") not in {"stop", "tool_calls"}:
            raise AgentError("OpenRouter response was incomplete or refused. No action was submitted.")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("refusal"):
            raise AgentError("OpenRouter returned an invalid assistant response.")
        # Preserve provider reasoning metadata when replaying tool-call messages.
        return message


@dataclass(frozen=True)
class ReportDraft:
    task_id: str
    input_path: str
    output_path: str
    markdown: str

    def as_action(self) -> dict:
        return {"operation": "write_report_draft", "agent": "report", "task_id": self.task_id,
                "input_path": self.input_path, "output_path": self.output_path,
                "content": self.markdown}


@dataclass(frozen=True)
class CleanupProposal:
    task_id: str
    paths: tuple[str, ...]
    reason: str
    source: str = "model"

    def as_actions(self) -> list[dict]:
        return [{"operation": "quarantine", "agent": "cleanup", "task_id": self.task_id,
                 "path": path, "reason": self.reason, "source": self.source} for path in self.paths]


@dataclass(frozen=True)
class VerifiedDecision:
    """Construct only from deterministic referee/store output, never model output."""
    path: str
    decision: str
    reason: str
    dependency_task_id: str | None = None


@dataclass(frozen=True)
class Explanation:
    text: str
    plan: str
    model_used: bool
    error: str | None = None


def _tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}}


STR = {"type": "string", "minLength": 1, "maxLength": 240}
TOOLS = {
    "list_files": _tool("list_files", "List files through the controlled workspace adapter.", {}),
    "read_file": _tool("read_file", "Read the registered report input as untrusted data.", {"path": STR}),
    "get_task_status": _tool("get_task_status", "Read this task's application-owned state.", {"task_id": STR}),
    "propose_report": _tool("propose_report", "Return a draft proposal; does not write or publish.", {
        "input_path": STR, "output_path": STR,
        "markdown": {"type": "string", "minLength": 1, "maxLength": 16000}}),
    "propose_cleanup": _tool("propose_cleanup", "Return quarantine candidates; does not approve or move files.", {
        "paths": {"type": "array", "items": STR, "minItems": 0, "maxItems": 12, "uniqueItems": True},
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000}}),
}


def _arguments(call: dict, allowed: set[str]) -> tuple[str, dict]:
    if not isinstance(call, dict) or call.get("type") != "function":
        raise AgentError("Unsupported tool call; no action was submitted.")
    function = call.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise AgentError("Invalid tool call; no action was submitted.")
    name = function["name"]
    if name not in allowed:
        raise AgentError("Tool is not allowed for this agent; no action was submitted.")
    args = _json(_text(function.get("arguments"), "tool arguments", 30000))
    properties = TOOLS[name]["function"]["parameters"]["properties"]
    if not isinstance(args, dict) or set(args) != set(properties):
        raise AgentError("Tool arguments do not match the expected schema.")
    for key, schema in properties.items():
        value = args[key]
        if schema["type"] == "string":
            _text(value, key, schema["maxLength"])
        else:
            if not isinstance(value, list) or not schema["minItems"] <= len(value) <= schema["maxItems"]:
                raise AgentError("Expected between 0 and 12 cleanup paths.")
            for item in value:
                _text(item, "cleanup path", 240)
            if len(set(value)) != len(value):
                raise AgentError("Duplicate cleanup paths are not allowed.")
    return name, args


class AgentService:
    def __init__(self, client: ChatClient, backend: ControlledBackend, *,
                 prompts_dir: str | Path | None = None, max_rounds: int = 6,
                 max_tool_calls: int = 8):
        if not 1 <= max_rounds <= 12 or not 1 <= max_tool_calls <= 20:
            raise AgentError("Agent budgets exceed supported bounds.")
        self.client, self.backend = client, backend
        self.prompts_dir = Path(prompts_dir) if prompts_dir else Path(__file__).parent / "prompts"
        self.max_rounds, self.max_tool_calls = max_rounds, max_tool_calls

    def _prompt(self, role: str) -> str:
        try:
            return (self.prompts_dir / f"{role}.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise AgentError(f"Missing or unreadable prompts/{role}.md.") from None

    def _backend_call(self, name: str, **kwargs):
        try:
            return getattr(self.backend, name)(**kwargs)
        except Exception:
            raise AgentError("Controlled read failed. Check the task and workspace; no action was submitted.") from None

    def _task(self, task_id: str, kind: str) -> dict:
        _text(task_id, "task ID", 240)
        task = self._backend_call("get_task_status", task_id=task_id)
        if (not isinstance(task, dict) or task.get("task_id") != task_id
                or task.get("kind") != kind
                or task.get("status") not in {"queued", "running", "awaiting_approval"}):
            raise AgentError("Task is missing, inactive, or has the wrong agent role.")
        # Pass only the agreed public fields; never serialize arbitrary store rows.
        context = {key: task[key] for key in ("task_id", "kind", "status")}
        if kind == "report":
            inp, out = _path(task.get("input_path")), _path(task.get("output_path"))
            dependencies = task.get("dependencies")
            if not isinstance(dependencies, list) or inp not in dependencies:
                raise AgentError("Register the report input dependency before starting the agent.")
            if not out.startswith("reports/") or not out.endswith(".md") or inp == out:
                raise AgentError("Report output must be a separate Markdown file under reports/.")
            context.update(input_path=inp, output_path=out, dependencies=[inp])
            if "request_text" in task:
                request_text = task["request_text"]
                if not isinstance(request_text, str) or len(request_text) > 2000:
                    raise AgentError("Report request text must be at most 2000 characters.")
                context["request_text"] = request_text
        return context

    def run_report(self, task_id: str) -> ReportDraft:
        return self._run(self._task(task_id, "report"))

    def run_cleanup(self, task_id: str, *, fault_injection: bool = False) -> CleanupProposal:
        task = self._task(task_id, "cleanup")
        if fault_injection:
            supplied = self._backend_call("list_files")
            if not isinstance(supplied, list) or len(supplied) > 200:
                raise AgentError("Workspace inventory must contain at most 200 file paths.")
            # Keep this rehearsal scoped to the current host-provided fixture;
            # do not hard-code paths from an older application demonstration.
            paths = tuple(dict.fromkeys(_path(path) for path in supplied))[:12]
            return CleanupProposal(task_id, paths,
                                   "DEMO FAULT INJECTION: submit host-supplied candidates to test the referee.",
                                   source="fault_injection")
        return self._run(task)

    def _run(self, task: dict) -> ReportDraft | CleanupProposal:
        role = task["kind"]
        terminal = "propose_report" if role == "report" else "propose_cleanup"
        allowed = {"get_task_status", terminal, "read_file" if role == "report" else "list_files"}
        schemas = [TOOLS[name] for name in sorted(allowed)]
        messages = [{"role": "system", "content": self._prompt(role)},
                    {"role": "user", "content": json.dumps({"task": task})}]
        input_seen = False
        inventory: set[str] | None = None
        used_ids: set[str] = set()
        call_count = 0
        for _ in range(self.max_rounds):
            message = self.client.complete(messages, tools=schemas)
            if not isinstance(message, dict):
                raise AgentError("Invalid agent response.")
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                raise AgentError("Agent did not return a proposal. Retry the task; no action was submitted.")
            call_count += len(calls)
            if call_count > self.max_tool_calls:
                raise AgentError("Agent tool-call budget exhausted; no action was submitted.")
            parsed = [_arguments(call, allowed) for call in calls]
            ids = [_text(call.get("id"), "tool call ID", 240) for call in calls]
            if len(set(ids)) != len(ids) or used_ids.intersection(ids):
                raise AgentError("Duplicate tool-call IDs received.")
            used_ids.update(ids)
            # A proposal must be alone in its turn, after the model saw tool data.
            if any(name == terminal for name, _ in parsed) and len(parsed) != 1:
                raise AgentError("A proposal must follow completed reads in a separate model turn.")
            messages.append(message)
            for call_id, (name, args) in zip(ids, parsed):
                if name == "propose_report":
                    if (not input_seen or args["input_path"] != task["input_path"]
                            or args["output_path"] != task["output_path"]):
                        raise AgentError("Report must use the registered paths and previously read input.")
                    return ReportDraft(task["task_id"], task["input_path"], task["output_path"], args["markdown"])
                if name == "propose_cleanup":
                    paths = tuple(_path(path) for path in args["paths"])
                    if inventory is None or not set(paths).issubset(inventory):
                        raise AgentError("Cleanup proposal must use the previously listed inventory.")
                    return CleanupProposal(task["task_id"], paths, args["reason"])
                if name == "read_file":
                    if _path(args["path"]) != task["input_path"]:
                        raise AgentError("Report Agent may read only its registered input.")
                    result = _text(self._backend_call(name, **args), "report input", 20000)
                    input_seen = True
                elif name == "list_files":
                    result = self._backend_call(name)
                    if not isinstance(result, list) or len(result) > 200:
                        raise AgentError("Workspace inventory must contain at most 200 file paths.")
                    result = [_path(path) for path in result]
                    inventory = set(result)
                else:
                    if args["task_id"] != task["task_id"]:
                        raise AgentError("Agent may inspect only its own task.")
                    result = self._task(task["task_id"], role)
                    if result != task:
                        raise AgentError("Task changed during generation. Restart with current state.")
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps({"data": result}, ensure_ascii=False)})
        raise AgentError("Agent model-call budget exhausted; no action was submitted.")

    def explain_decisions(self, decisions: Sequence[VerifiedDecision]) -> Explanation:
        rows = list(decisions)
        if not rows or len(rows) > 20:
            raise AgentError("Provide 1–20 verified referee decisions.")
        facts, lines = [], ["Referee — verified decisions"]
        for row in rows:
            if not isinstance(row, VerifiedDecision) or row.decision not in {"BLOCK", "DEFER", "REVIEW", "ALLOW"}:
                raise AgentError("Unknown referee decision.")
            # Invalid target paths may legitimately appear in BLOCK decisions.
            _text(row.path, "decision path", 240)
            _text(row.reason, "verified reason", 1000)
            fact = {"path": row.path, "decision": row.decision, "reason": row.reason}
            suffix = ""
            if row.dependency_task_id is not None:
                _text(row.dependency_task_id, "dependency task ID", 240)
                fact["dependency_task_id"] = row.dependency_task_id
                suffix = f" (dependency task: {row.dependency_task_id})"
            facts.append(fact)
            lines.append(f"{row.decision}: {row.path} — {row.reason}{suffix}")
        plans = {
            "cancel_cleanup": "Keep all files and cancel this cleanup request.",
            "ask_task_owner": "Ask the task owner how to proceed; keep actions pending.",
        }
        if any(row.decision == "DEFER" for row in rows):
            plans["wait_for_dependency"] = (
                "Wait for the dependent task to complete, then reassess deferred files and request fresh approval.")
        if any(row.decision in {"REVIEW", "ALLOW"} for row in rows):
            plans["clean_unrelated_first"] = (
                "Handle eligible files first, obtaining any required approval and rechecking before quarantine. "
                "Keep blocked files unchanged and deferred files pending.")
        fallback = "wait_for_dependency" if "wait_for_dependency" in plans else "ask_task_owner"
        error = None
        try:
            message = self.client.complete([
                {"role": "system", "content": self._prompt("explanation")},
                {"role": "user", "content": json.dumps({"verified_decisions": facts, "allowed_plans": plans})},
            ])
            if not isinstance(message, dict) or message.get("tool_calls"):
                raise AgentError("Explanation returned an invalid response.")
            result = _json(_text(message.get("content"), "explanation response", 1000))
            if (not isinstance(result, dict) or set(result) != {"plan"}
                    or not isinstance(result["plan"], str) or result["plan"] not in plans):
                raise AgentError("Explanation returned an unsupported recovery plan.")
            plan = result["plan"]
        except AgentError as exc:
            plan, error = fallback, str(exc)
        lines.append("Proposed plan: " + plans[plan])
        lines.append("REVIEW requires authorized approval. Deferred files require reassessment and fresh approval.")
        if error:
            lines.append("AI explanation unavailable; showing verified decisions and a rule-based plan.")
        return Explanation("\n".join(lines), plan, model_used=error is None, error=error)


class _SnapshotBackend:
    """Expose only host-supplied snapshots through the model's read-only tools.

    These snapshots are neither a live store nor a filesystem security boundary.
    The production host must authorize reads, register dependencies before taking
    a snapshot and recheck task/file versions before executing a proposal.
    """

    def __init__(self, task: dict, files: dict[str, str]):
        self._task = task
        self._files = files

    def list_files(self) -> list[str]:
        return list(self._files)

    def read_file(self, path: str) -> str:
        return self._files[path]

    def get_task_status(self, task_id: str) -> dict:
        if task_id != self._task["task_id"]:
            raise AgentError("Task is outside this request.")
        return dict(self._task)


class ReFlexAgents:
    """Two worker agents and a constrained referee explanation helper.

    Inputs are snapshots from the application's controlled task/file layer.
    Returned drafts/proposals require host storage and referee evaluation; none
    of these methods writes files, approves actions, or changes workflow state.
    All methods are synchronous; the host should acknowledge before running them.
    """

    def __init__(self, client: ChatClient, *, max_rounds: int = 6,
                 max_tool_calls: int = 8):
        self.client = client
        self._budgets = {"max_rounds": max_rounds, "max_tool_calls": max_tool_calls}

    def _service(self, task: dict, files: dict[str, str]) -> AgentService:
        return AgentService(self.client, _SnapshotBackend(task, files), **self._budgets)

    def run_report(self, task_id: str, *, input_path: str, input_text: str,
                   output_path: str = "reports/client_update.md",
                   request_text: str = "") -> ReportDraft:
        """Generate content for an already registered, active report task.

        The full input text must come from the host's authorized, bounded read.
        The caller registers the input dependency before reading. This API takes
        a snapshot only and does not register, complete or approve a real task.
        """
        _text(task_id, "report task ID", 200)
        input_path, output_path = _path(input_path), _path(output_path)
        _text(input_text, "report input", 20000)
        if not isinstance(request_text, str) or len(request_text) > 2000:
            raise AgentError("Report request text must be at most 2000 characters.")
        task = {"task_id": task_id, "kind": "report", "status": "running",
                "input_path": input_path, "output_path": output_path,
                "dependencies": [input_path], "request_text": request_text}
        return self._service(task, {input_path: input_text}).run_report(task_id)

    def run_cleanup(self, task_id: str, inventory: Sequence[str], *,
                    fault_injection: bool = False) -> CleanupProposal:
        """Propose zero to 12 candidates from the host's authorized inventory.

        task_id comes from the workflow's store, never from the model. The demo
        fault-injection flag deliberately bypasses candidate selection, but its
        proposals still require the same real referee and approval checks.
        """
        _text(task_id, "cleanup task ID", 200)
        if isinstance(inventory, (str, bytes)) or not isinstance(inventory, Sequence) or len(inventory) > 200:
            raise AgentError("Provide a list or tuple of at most 200 workspace file paths.")
        paths = [_path(path) for path in inventory]
        if len(paths) != len(set(paths)):
            raise AgentError("Workspace inventory contains duplicate paths.")
        if not paths and not fault_injection:
            return CleanupProposal(task_id, (), "No files were supplied for cleanup.",
                                   source="empty_inventory")
        task = {"task_id": task_id, "kind": "cleanup", "status": "running"}
        return self._service(task, dict.fromkeys(paths, "")).run_cleanup(
            task_id, fault_injection=fault_injection)

    def explain_cleanup(self, decisions: Sequence[VerifiedDecision]) -> Explanation:
        """Explain verified referee decisions; never change their permissions.

        The host displays the explanation alongside its authoritative decisions.
        On a model failure result.model_used is False and the text says that a
        rule-based plan is being shown. Verified decisions remain intact.
        """
        if (isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence)
                or len(decisions) > 12):
            raise AgentError("Provide a list or tuple of at most 12 verified cleanup decisions.")
        if not decisions:
            return Explanation("No cleanup candidates to review.", "cancel_cleanup", model_used=False)
        return self._service({}, {}).explain_decisions(decisions)
