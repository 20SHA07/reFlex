"""Local, disposable demo bridge for the reFlex browser extension.

This hosts the team's real controlled executor. It neither authenticates a Slack
user nor attaches to an arbitrary workspace. Every launch creates fresh fixtures.
Only the locally paired owner can review actions. Two proposal-only test agents
can use offline samples or an explicitly configured OpenRouter model.
"""
from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import uuid

# The team executor currently uses a sibling import, so load it from its own
# directory without changing any of the team's files.
sys.path.insert(0, str(Path(__file__).resolve().parent / "referee_agent"))
from executor import RefereeService, SafetyError  # noqa: E402
from openrouter_agents import DEFAULT_MODEL, OpenRouterAgents, OpenRouterError  # noqa: E402
from test_agents import CleanupAgent, ReportAgent  # noqa: E402

MAX_BODY_BYTES = 16_384
MAX_CONTEXTS = 32
MAX_REQUESTS = 512
ID_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,63}\Z")
PRINCIPAL = {"id": "local-owner", "display_name": "Local demo owner"}
PATHS = ("data/source_metrics.csv", "working/report_input.csv", "scratch/debug.log")


class BridgeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _revision(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _context(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise BridgeError("invalid_context", "Open a Slack channel and refresh its context.")
    workspace, channel = value.get("workspace_id"), value.get("channel_id")
    if not all(isinstance(v, str) and ID_PATTERN.fullmatch(v) for v in (workspace, channel)):
        raise BridgeError("invalid_context", "A valid Slack workspace and channel are required.")
    if not workspace.startswith("T") or not channel.startswith(("C", "G", "D")):
        raise BridgeError("invalid_context", "A valid Slack workspace and channel are required.")
    url = value.get("url")
    if not isinstance(url, str) or len(url) > 2048:
        raise BridgeError("invalid_context", "The Slack channel URL is required.")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "app.slack.com"
            or parsed.path.rstrip("/") != f"/client/{workspace}/{channel}"):
        raise BridgeError("invalid_context", "The channel URL and channel identifiers do not match.")
    return workspace, channel


class DemoSession:
    def __init__(self, root: Path, key: tuple[str, str], agent_provider=None):
        # Workers receive proposal-generation data only, never the executor or
        # an approval callback. The host assigns both their identities.
        self.report_agent = ReportAgent(agent_provider)
        self.cleanup_agent = CleanupAgent(agent_provider)
        self.workspace = root / "workspace"
        for folder in ("data", "working", "scratch", "reports"):
            (self.workspace / folder).mkdir(parents=True)
        metrics = "day,count\nMonday,12\nTuesday,18\nWednesday,15\n"
        (self.workspace / PATHS[0]).write_text(metrics, encoding="utf-8")
        (self.workspace / PATHS[1]).write_text(metrics, encoding="utf-8")
        (self.workspace / PATHS[2]).write_text("Disposable demo debug log.\n", encoding="utf-8")
        self.referee = RefereeService(self.workspace, root / "private_state",
                                      allowed_approver_ids={PRINCIPAL["id"]})
        self.state = {
            "mode": "local-demo", "principal": dict(PRINCIPAL),
            "agent": dict(self.report_agent.descriptor),
            "context": {"workspace_id": key[0], "channel_id": key[1]},
            "report": None, "cleanup": None, "events": [],
            "message": "Local fixture ready. Start the report agent, then review the cleanup agent's proposals.",
        }
        self.requests: dict[str, str] = {}
        self.report_task: str | None = None
        self.report_source_hash: str | None = None
        self.restart_required = False
        if agent_provider is None:
            self.event("info", "Two test agents connected to disposable files. They use offline samples, without an AI model.")
        else:
            self.event("info", "Two OpenRouter test agents connected to disposable files. Start sends request text and demo report data to the configured model.")

    def event(self, kind: str, text: str) -> None:
        self.state["events"].append({"id": uuid.uuid4().hex, "kind": kind, "text": text,
                                     "at": datetime.now(timezone.utc).isoformat()})
        self.state["events"] = self.state["events"][-20:]
        self.state["message"] = text

    @staticmethod
    def cleanup_item(decision: dict, proposal_reason: str = "") -> dict:
        return {
            "id": decision["action_id"],
            "revision": _revision([decision["action_id"], decision["expected_hash"]]),
            "path": decision["path"], "verdict": decision["verdict"],
            "reason": decision["explanation"], "executed": bool(decision["executed"]),
            "proposal_reason": proposal_reason,
        }

    def start_report(self, text: str) -> None:
        if self.state["report"] is not None:
            raise BridgeError("report_exists", "This demo already has a report. Review it or restart the bridge for a fresh fixture.", 409)
        try:
            self._build_report(text)
        except OpenRouterError as exc:
            # Public provider errors contain no upstream response or credential.
            self.restart_required = self.report_task is not None
            message = f"{exc} The input remains reserved and this fixture is paused; restart the bridge for a fresh demo."
            self.event("warning", message)
            raise BridgeError(exc.code, message, 502) from exc
        except (BridgeError, SafetyError, OSError, ValueError, KeyError) as exc:
            if self.report_task is not None:
                # A read/generation failure must not silently release an input
                # lease or leave an apparently usable workflow with a hidden task.
                self.restart_required = True
                message = "Report generation failed after reserving its input. The fixture is paused; restart the bridge for a fresh demo."
                self.event("warning", message)
                raise BridgeError("restart_required", message, 409) from exc
            raise

    def _build_report(self, text: str) -> None:
        # Register the dependency before reading, using a host-assigned identity.
        task_id = uuid.uuid4().hex
        self.referee.register_task(task_id, PRINCIPAL["id"], [PATHS[1]])
        self.report_task = task_id
        # Cleanup might have been reviewed first. Replace its now-outdated
        # eligible input review as soon as the report registers a dependency.
        cleanup = self.state["cleanup"]
        if cleanup:
            for index, item in enumerate(cleanup["items"]):
                if item["path"] == PATHS[1] and not item["executed"]:
                    decision = self.referee.evaluate_action("quarantine", PATHS[1],
                        agent_id=self.cleanup_agent.agent_id, requested_by_slack_id=PRINCIPAL["id"],
                        expected_hash=self.referee.fingerprint(PATHS[1]),
                        reason="Refresh cleanup after the report reserves its working input.")
                    cleanup["items"][index] = self.cleanup_item(decision, item.get("proposal_reason", ""))
                    self.event("info", "Working-input cleanup now waits for the report. The earlier review has been replaced.")
        source = self.referee.read_text(PATHS[1])
        self.report_source_hash = source["sha256"]
        draft = self.report_agent.run(input_path=PATHS[1], source_text=source["text"], request_text=text)
        output_path = f"reports/client_update-{uuid.uuid4().hex}.md"
        decision = self.referee.evaluate_action("create", output_path,
            agent_id=self.report_agent.agent_id, requested_by_slack_id=PRINCIPAL["id"], content=draft,
            reason="Publish this exact report draft only after local owner review.")
        if decision["verdict"] != "REVIEW":
            raise BridgeError("report_blocked", "The executor blocked this report. Restart with a fresh fixture.", 409)
        self.state["report"] = {
            "id": decision["action_id"], "revision": _revision([decision["action_id"], source["sha256"], draft]),
            "status": "awaiting_approval", "input_path": PATHS[1],
            "output_path": output_path, "draft": draft,
        }
        self.event("info", "Report drafted. Its working input is reserved until the owner approves publication.")

    def start_cleanup(self, text: str = "") -> None:
        # These metadata snapshots precede model generation. If a file changes
        # during the call, its original hash still governs review and approval.
        # Cleanup receives no file bodies or access outside these three paths.
        candidates = []
        for path in PATHS:
            try:
                source = self.referee.read_text(path)
                candidate = {"path": path, "size_bytes": len(source["text"].encode("utf-8")), "sha256": source["sha256"]}
            except (SafetyError, OSError, UnicodeError):
                candidate = {"path": path, "size_bytes": None, "sha256": None}
            candidates.append(candidate)
        try:
            proposals = self.cleanup_agent.run(candidates=copy.deepcopy(candidates), request_text=text)
        except OpenRouterError as exc:
            # No evaluate_action call or replacement of a previous review has
            # happened yet. A failed model request cannot partially apply a plan.
            raise BridgeError(exc.code, str(exc), 502) from exc
        previous = {item["path"]: item for item in (self.state["cleanup"] or {}).get("items", [])}
        by_path = {item["path"]: item for item in proposals}
        items = []
        for candidate in candidates:
            path = candidate["path"]
            if previous.get(path, {}).get("executed"):
                items.append(previous[path])
                continue
            proposal_reason = by_path[path]["reason"]
            decision = self.referee.evaluate_action("quarantine", path,
                agent_id=self.cleanup_agent.agent_id, requested_by_slack_id=PRINCIPAL["id"],
                # Never pass None here: that would allow the executor to adopt
                # a file newly created during generation as its original input.
                expected_hash=candidate["sha256"] or "unavailable-before-generation",
                reason=proposal_reason)
            items.append(self.cleanup_item(decision, proposal_reason))
        self.state["cleanup"] = {"id": uuid.uuid4().hex, "items": items}
        self.event("info", "Cleanup reviewed. Protected files stay blocked; active inputs wait; eligible files need approval.")

    @staticmethod
    def check_target(target: object, stored: dict | None) -> None:
        if (not isinstance(target, dict) or stored is None
                or target.get("id") != stored["id"] or target.get("revision") != stored["revision"]):
            raise BridgeError("stale_review", "This review is no longer current. Refresh and review the new proposal.", 409)

    def approve_report(self, target: object) -> None:
        report = self.state["report"]
        self.check_target(target, report)
        if report["status"] == "completed":
            return
        # A stored draft is only valid for the exact input snapshot it reviewed.
        try:
            if self.referee.fingerprint(PATHS[1]) != self.report_source_hash:
                raise BridgeError("stale_review", "The report input changed. Restart the disposable demo to review a fresh report.", 409)
        except SafetyError:
            raise BridgeError("stale_review", "The report input is unavailable. Restart the disposable demo.", 409)
        result = self.referee.execute_approved_action(report["id"], PRINCIPAL["id"])
        if not result["executed"]:
            raise BridgeError("execution_blocked", "The executor blocked publication. The input remains reserved.", 409)
        published = self.referee.read_text(report["output_path"])
        if published["text"] != report["draft"]:
            raise BridgeError("publication_changed", "Published output differs from the reviewed report. Its input remains reserved.", 409)
        self.referee.complete_task(self.report_task, PRINCIPAL["id"])
        report["status"] = "completed"
        self.event("success", f"Approved report published to {report['output_path']}. Its input dependency is released.")
        if result.get("audit_warning"):
            self.event("warning", "The report was published, but its completion journal could not be written. Inspect the local audit files.")
        cleanup = self.state["cleanup"]
        if cleanup:
            for index, item in enumerate(cleanup["items"]):
                if item["verdict"] == "DEFER":
                    fresh = self.referee.reevaluate_action(item["id"])
                    cleanup["items"][index] = self.cleanup_item(fresh, item.get("proposal_reason", ""))
                    self.event("info", f"{item['path']} has a fresh {fresh['verdict']} decision. Any eligible cleanup needs a new approval.")

    def approve_cleanup(self, target: object) -> None:
        cleanup = self.state["cleanup"]
        item = next((entry for entry in (cleanup or {}).get("items", [])
                     if isinstance(target, dict) and entry["id"] == target.get("id")), None)
        self.check_target(target, item)
        if item["executed"]:
            return
        if item["verdict"] != "REVIEW":
            raise BridgeError("approval_unavailable", "This file is blocked or still required by a task. Approval is unavailable.", 409)
        result = self.referee.execute_approved_action(item["id"], PRINCIPAL["id"])
        item.update(self.cleanup_item(result, item.get("proposal_reason", "")))
        if not result["executed"]:
            self.event("warning", f"Cleanup prevented for {item['path']}. Request a fresh review.")
            raise BridgeError("stale_review", "The executor prevented cleanup. Refresh and request a fresh review.", 409)
        self.event("success", f"Approved cleanup moved {item['path']} to quarantine. No permanent deletion.")
        if result.get("audit_warning"):
            self.event("warning", "Cleanup completed, but its completion journal could not be written. Inspect the local audit files.")


class BrowserBridge:
    def __init__(self, agent_provider=None):
        self.agent_provider = agent_provider
        self.agent = dict(agent_provider.descriptor) if agent_provider is not None else {"provider": "sample", "model": None}
        self.root = Path(tempfile.mkdtemp(prefix="reflex_browser_demo_"))
        self.root.chmod(0o700)
        self.token = secrets.token_urlsafe(32)
        self.token_path = self.root / "pairing-token.txt"
        with self.token_path.open("x", encoding="utf-8") as handle:
            os.chmod(self.token_path, 0o600)
            handle.write(self.token + "\n")
        self.sessions: dict[tuple[str, str], DemoSession] = {}
        self.lock = threading.RLock()

    def authenticated(self, header: str | None) -> bool:
        if not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        candidate = header[7:]
        return len(candidate) <= 256 and hmac.compare_digest(candidate.encode(), self.token.encode())

    def dispatch(self, body: object) -> dict:
        if not isinstance(body, dict):
            raise BridgeError("invalid_request", "Send a JSON object.")
        operation = body.get("operation")
        if not isinstance(operation, str) or operation not in {"status", "start_report", "start_cleanup", "approve_report", "approve_cleanup"}:
            raise BridgeError("invalid_operation", "Unknown browser bridge operation.")
        key = _context(body.get("context"))
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise BridgeError("invalid_request", "A short request identifier is required.")
        # No identity from the page or request is used. Token authentication has
        # already established the single local owner at the HTTP boundary.
        supplied_input = body.get("input", {})
        if not isinstance(supplied_input, dict):
            raise BridgeError("invalid_input", "Input must be an object.")
        text = supplied_input.get("text", "")
        if not isinstance(text, str) or len(text) > 2000:
            raise BridgeError("invalid_input", "Request text must be at most 2000 characters.")
        fingerprint = _revision([operation, text, body.get("target")])
        with self.lock:
            session = self.sessions.get(key)
            if session is None:
                if len(self.sessions) >= MAX_CONTEXTS:
                    raise BridgeError("context_limit", "Restart this demo bridge before adding more channels.", 429)
                session = DemoSession(self.root / uuid.uuid4().hex, key, self.agent_provider)
                self.sessions[key] = session
            if session.restart_required and operation != "status":
                raise BridgeError("restart_required", "This fixture is paused after a report failure. Restart the bridge for a fresh demo.", 409)
            if operation != "status":
                previous = session.requests.get(request_id)
                if previous is not None:
                    if previous != fingerprint:
                        raise BridgeError("request_conflict", "This request identifier was already used for different input.", 409)
                    return {"ok": True, "state": copy.deepcopy(session.state)}
                if len(session.requests) >= MAX_REQUESTS:
                    raise BridgeError("request_limit", "Restart this demo bridge for a fresh session.", 429)
            try:
                if operation == "start_report":
                    session.start_report(text)
                elif operation == "start_cleanup":
                    session.start_cleanup(text)
                elif operation == "approve_report":
                    session.approve_report(body.get("target"))
                elif operation == "approve_cleanup":
                    session.approve_cleanup(body.get("target"))
            except SafetyError as exc:
                raise BridgeError(exc.code, "A sample file changed or is unavailable. Restart the demo for a fresh fixture.", 409) from exc
            if operation != "status":
                session.requests[request_id] = fingerprint
            return {"ok": True, "state": copy.deepcopy(session.state)}


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bridge: BrowserBridge, *, port: int = 8765, extension_id: str | None = None):
        if extension_id is not None and not re.fullmatch(r"[a-p]{32}", extension_id):
            raise ValueError("The extension ID must be the 32-letter ID from chrome://extensions.")
        self.bridge = bridge
        self.allowed_origin = f"chrome-extension://{extension_id}" if extension_id else None
        super().__init__(("127.0.0.1", port), BridgeHandler)


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        # URLs and headers may contain sensitive input. Do not log requests.
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _error(self, exc: BridgeError) -> None:
        self._respond(exc.status, {"ok": False, "error": {"code": exc.code, "message": str(exc)}})

    def _respond(self, status: int, value: dict | None) -> None:
        data = json.dumps(value, ensure_ascii=True).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        origin = self.headers.get("Origin")
        if origin and origin == self.server.allowed_origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _boundary(self, *, preflight: bool = False) -> None:
        expected_host = f"127.0.0.1:{self.server.server_address[1]}"
        if self.headers.get_all("Host") != [expected_host]:
            raise BridgeError("invalid_host", "Use the fixed loopback bridge address.", 403)
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != self.server.allowed_origin):
            raise BridgeError("invalid_origin", "This origin is not paired with the local bridge.", 403)
        # Some privileged extension service-worker requests omit Origin. A bearer
        # token remains mandatory. An ordinary webpage cannot send an authorized
        # cross-origin request because its preflight is rejected.
        if not preflight and (len(self.headers.get_all("Authorization", [])) != 1
                or not self.server.bridge.authenticated(self.headers.get("Authorization"))):
            raise BridgeError("unauthorized", "Pair with the current local demo token.", 401)

    def do_OPTIONS(self):
        try:
            self._boundary(preflight=True)
            if self.path not in {"/v1/session", "/v1/dispatch"}:
                raise BridgeError("not_found", "Unknown bridge route.", 404)
            if not self.headers.get("Origin") or not self.server.allowed_origin:
                raise BridgeError("invalid_origin", "Configure the extension ID before pairing.", 403)
            if self.headers.get("Access-Control-Request-Method") not in {"GET", "POST"}:
                raise BridgeError("invalid_method", "Unsupported request method.", 405)
            requested_headers = {name.strip().lower() for name in self.headers.get("Access-Control-Request-Headers", "").split(",") if name.strip()}
            if not requested_headers <= {"authorization", "content-type"}:
                raise BridgeError("invalid_headers", "Unsupported request headers.", 403)
            self._respond(204, None)
        except BridgeError as exc:
            self._error(exc)

    def do_GET(self):
        try:
            self._boundary()
            if self.path != "/v1/session":
                raise BridgeError("not_found", "Unknown bridge route.", 404)
            self._respond(200, {"ok": True, "principal": dict(PRINCIPAL), "mode": "local-demo", "agent": dict(self.server.bridge.agent)})
        except BridgeError as exc:
            self._error(exc)

    def do_POST(self):
        try:
            self._boundary()
            if self.path != "/v1/dispatch":
                raise BridgeError("not_found", "Unknown bridge route.", 404)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise BridgeError("invalid_content_type", "Use application/json.", 415)
            if self.headers.get("Transfer-Encoding"):
                raise BridgeError("invalid_request", "Chunked request bodies are not supported.")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise BridgeError("invalid_request", "Provide one valid Content-Length.")
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                raise BridgeError("body_too_large", "The request body is empty or exceeds the demo limit.", 413)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                body = json.loads(raw)
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise BridgeError("invalid_json", "Send a complete UTF-8 JSON object.")
            self._respond(200, self.server.bridge.dispatch(body))
        except BridgeError as exc:
            self._error(exc)
        except (OSError, ValueError, KeyError):
            self._error(BridgeError("demo_unavailable", "The local fixture is unavailable. Restart the bridge for a fresh demo.", 503))


def provider_from_args(args: argparse.Namespace):
    """Opt in explicitly; read credentials locally and never accept key flags."""
    if not args.openrouter:
        if args.model:
            raise ValueError("Use --openrouter with --model to enable that model.")
        return None
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        if not sys.stdin.isatty():
            raise ValueError("OPENROUTER_API_KEY is required with --openrouter. Set it in the backend environment or start from an interactive terminal for a private prompt.")
        try:
            key = getpass.getpass("OpenRouter API key (hidden, backend only): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise ValueError("OpenRouter key entry cancelled. No backend was started.") from None
        if not key:
            raise ValueError("An OpenRouter API key is required. No backend was started.")
    model = args.model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
    return OpenRouterAgents(api_key=key, model=model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-id", help="ID shown in edge://extensions or chrome://extensions; permits only that extension Origin.")
    parser.add_argument("--openrouter", action="store_true", help="Run both test agents through OpenRouter instead of offline sample generation.")
    parser.add_argument("--model", help=f"OpenRouter model ID (or OPENROUTER_MODEL); default: {DEFAULT_MODEL}.")
    args = parser.parse_args()
    try:
        agent_provider = provider_from_args(args)
        if args.extension_id is not None and not re.fullmatch(r"[a-p]{32}", args.extension_id):
            raise ValueError("The extension ID must be the 32-letter ID from edge://extensions or chrome://extensions.")
    except (OpenRouterError, ValueError) as exc:
        parser.error(str(exc))
    bridge = BrowserBridge(agent_provider=agent_provider)
    try:
        server = BridgeServer(bridge, extension_id=args.extension_id)
    except OSError:
        bridge.token_path.unlink(missing_ok=True)
        parser.error("Cannot start the bridge on 127.0.0.1:8765. Stop any existing bridge, then retry.")
    print("reFlex local demo bridge: http://127.0.0.1:8765", flush=True)
    print(f"Pairing token file (private; copy its contents into extension settings): {bridge.token_path}", flush=True)
    print(f"Disposable fixtures and audit files: {bridge.root}", flush=True)
    if agent_provider is None:
        print("Local demo owner only. Slack context is not identity. Sample reports do not use an AI model.", flush=True)
    else:
        print(f"Local demo owner only. OpenRouter model: {agent_provider.model}. Two test agents propose; the referee and human approve.", flush=True)
        print("Start sends selected request text and demo report data to OpenRouter. This host only operates on disposable fixtures.", flush=True)
    if not args.extension_id:
        print("If Chrome sends an Origin header, restart with --extension-id YOUR_EXTENSION_ID.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Keep fixture/audit evidence for inspection, invalidate the old secret.
        bridge.token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
