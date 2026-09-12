"""Single-process Agent Referee service and controlled executor.

TRUST BOUNDARY: host calls register_task/complete_task and authenticates Slack
users. Agents only receive a wrapper around evaluate_action. Do not expose
execute_approved_action or unrestricted filesystem/shell tools to workers.

Task/action state is in memory. An append-only JSONL journal persists evidence,
not resumable state. Do not resume old Slack approvals after a process restart.
This is not an OS sandbox, a distributed lock, or crash-atomic storage.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from referee import Action, Decision, SUPPORTED_OPERATIONS, TaskLease, evaluate, is_protected


class SafetyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _relative(path: str) -> str:
    # Use repository-style / separators on all platforms. Reject ambiguity.
    if not isinstance(path, str) or not path or len(path) > 512:
        raise SafetyError("invalid_path", "Provide a nonempty relative workspace path.")
    if "\\" in path or ":" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise SafetyError("invalid_path", "Use plain relative paths with / separators, no drive letters or control characters.")
    parts = path.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise SafetyError("path_escape", "Absolute paths, empty path parts, . and .. are not allowed.")
    # Prevent Windows alternate path/device names and trailing-dot ambiguity.
    devices = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if any(p.endswith((" ", ".")) or p.split(".")[0].upper() in devices for p in parts):
        raise SafetyError("invalid_path", "Ambiguous or reserved filename.")
    return "/".join(parts)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RefereeService:
    def __init__(self, workspace: str | Path, state_dir: str | Path, *,
                 allowed_approver_ids: set[str], protected_paths: tuple[str, ...] = ("data",),
                 max_file_bytes: int = 2_000_000):
        supplied_root = Path(workspace).absolute()
        if supplied_root.is_symlink():
            raise ValueError("Workspace root must not be a symlink.")
        self.workspace = supplied_root.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory.")
        if not allowed_approver_ids or any(not isinstance(x, str) or not x for x in allowed_approver_ids):
            raise ValueError("Configure at least one trusted human Slack ID.")
        self.allowed_approver_ids = frozenset(allowed_approver_ids)
        self.protected_paths = tuple(_relative(p) for p in protected_paths)
        self.max_file_bytes = max_file_bytes
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskLease] = {}
        self._completed_tasks: dict[str, TaskLease] = {}
        self._actions: dict[str, Action] = {}
        self._decisions: dict[str, Decision] = {}
        self._finished: dict[str, dict] = {}
        self._superseded: set[str] = set()

        supplied_state = Path(state_dir).absolute()
        if supplied_state.is_symlink():
            raise ValueError("State directory must not be a symlink.")
        proposed_state = supplied_state.resolve()
        if proposed_state == self.workspace or self.workspace in proposed_state.parents:
            raise ValueError("State and quarantine must be OUTSIDE the agent workspace.")
        supplied_state.mkdir(parents=True, exist_ok=True)
        self.state_dir = supplied_state.resolve(strict=True)
        self.quarantine = self.state_dir / "quarantine"
        if self.quarantine.is_symlink():
            raise ValueError("Quarantine must not be a symlink.")
        self.quarantine.mkdir(exist_ok=True)
        if self.workspace.stat().st_dev != self.quarantine.stat().st_dev:
            raise ValueError("Workspace and state must be on the same filesystem for rename-based quarantine.")
        self.audit_path = self.state_dir / "audit.jsonl"
        self._audit("service_started", workspace=str(self.workspace),
                    protected_paths=list(self.protected_paths))

    def _audit(self, event: str, **fields) -> str:
        if self.audit_path.is_symlink():
            raise OSError("Refusing symlink audit path")
        if self.audit_path.exists() and (not self.audit_path.is_file() or self.audit_path.stat().st_nlink != 1):
            raise OSError("Audit path must be a regular non-hardlinked file")
        audit_id = uuid.uuid4().hex
        record = {"audit_id": audit_id, "ts": time.time(), "event": event, **fields}
        # No raw file contents or credentials in journal records.
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return audit_id

    def _locate(self, relative: str, *, allow_missing: bool = False) -> Path:
        relative = _relative(relative)
        current = self.workspace
        parts = relative.split("/")
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if allow_missing and index == len(parts) - 1:
                    return current
                raise SafetyError("missing_file", f"File or parent directory not found: {relative}")
            if stat.S_ISLNK(info.st_mode) or getattr(current, "is_junction", lambda: False)():
                raise SafetyError("symlink_path", "Symlinks and directory junctions are not supported.")
            if index < len(parts) - 1:
                if not stat.S_ISDIR(info.st_mode):
                    raise SafetyError("invalid_path", "A parent path is not a directory.")
            elif not stat.S_ISREG(info.st_mode):
                raise SafetyError("not_regular_file", "Only individual regular files may be acted on.")
            elif info.st_nlink != 1:
                raise SafetyError("hardlinked_file", "Hardlinked files are not supported.")
        resolved = current.resolve(strict=True)
        if self.workspace not in resolved.parents:
            raise SafetyError("path_escape", "Path escapes the workspace.")
        return current

    def _read_bytes(self, path: Path) -> bytes:
        with path.open("rb") as handle:
            data = handle.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise SafetyError("file_too_large", "File exceeds the demo size limit.")
        return data

    def fingerprint(self, path: str) -> str:
        """Trusted read helper: capture a hash when a worker reads a file."""
        with self._lock:
            return _sha(self._read_bytes(self._locate(path)))

    def read_text(self, path: str) -> dict:
        """Read-only adapter for text fixtures. Protected means no mutation, not secret."""
        with self._lock:
            relative = _relative(path)
            data = self._read_bytes(self._locate(relative))
            return {"path": relative, "sha256": _sha(data), "text": data.decode("utf-8")}

    def register_task(self, task_id: str, owner_slack_id: str, required_files: list[str]) -> dict:
        """TRUSTED HOST ONLY: register before the agent begins reading inputs."""
        with self._lock:
            if owner_slack_id not in self.allowed_approver_ids:
                raise PermissionError("Task owner is not a configured human.")
            if not task_id or task_id in self._tasks or task_id in self._completed_tasks:
                raise ValueError("Task ID must be nonempty and new.")
            paths = tuple(sorted({_relative(p) for p in required_files}))
            if not paths:
                raise ValueError("Register at least one required input.")
            for path in paths:
                self._locate(path)
            task = TaskLease(task_id, owner_slack_id, paths)
            self._audit("task_registered", task_id=task_id, owner_slack_id=owner_slack_id,
                        required_files=list(paths))
            self._tasks[task_id] = task
            return {"task_id": task_id, "status": "active", "required_files": list(paths)}

    def complete_task(self, task_id: str, approver_slack_id: str) -> dict:
        """HOST ONLY after report publication succeeds and its owner approves.

        This method checks identity but does not infer whether the scientific or
        business task is complete. The integration workflow establishes that.
        """
        with self._lock:
            task = self._tasks.get(task_id) or self._completed_tasks.get(task_id)
            if task is None:
                raise KeyError("Unknown task")
            if approver_slack_id != task.owner_slack_id:
                raise PermissionError("Only this task's owner can complete it.")
            if task_id in self._completed_tasks:
                return {"task_id": task_id, "status": "completed", "already_completed": True}
            self._audit("task_completed", task_id=task_id, approved_by=approver_slack_id)
            self._completed_tasks[task_id] = self._tasks.pop(task_id)
            return {"task_id": task_id, "status": "completed", "already_completed": False}

    def _check(self, action: Action) -> Decision:
        try:
            _relative(action.path)
            policy = evaluate(action, self._tasks.values(), self.protected_paths)
            if policy.verdict == "BLOCK":
                return policy
            target = self._locate(action.path, allow_missing=action.operation == "create")
            if action.operation == "create":
                if target.exists():
                    raise SafetyError("target_exists", "Create cannot overwrite an existing file.")
            else:
                current_hash = _sha(self._read_bytes(target))
                if current_hash != action.expected_hash:
                    raise SafetyError("stale_file", "File contents changed. Submit and review a new proposal.")
            return policy
        except SafetyError as exc:
            return Decision(action.action_id, "BLOCK", exc.code, str(exc))
        except OSError as exc:
            return Decision(action.action_id, "BLOCK", "filesystem_error", str(exc))

    def _public(self, action: Action, decision: Decision) -> dict:
        return {**decision.to_dict(), "agent_id": action.agent_id,
                "requested_by_slack_id": action.requested_by_slack_id,
                "operation": action.operation, "path": action.path,
                "expected_hash": action.expected_hash, "content_hash": action.content_hash,
                "executed": False}

    def evaluate_action(self, operation: str, path: str, *, agent_id: str,
                        requested_by_slack_id: str, expected_hash: str | None = None,
                        content: str | None = None, reason: str = "") -> dict:
        """Store an immutable proposal and return a JSON-ready decision.

        agent_id and requested_by_slack_id MUST be supplied by the host wrapper,
        not taken from model arguments. All supported mutations require review.
        Existing-file proposals capture a content hash; a supplied read-time hash
        is checked as well. No operation is performed here.
        """
        with self._lock:
            if requested_by_slack_id not in self.allowed_approver_ids:
                raise PermissionError("Unknown requester; use verified Slack context.")
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError("Host must assign an agent ID.")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise ValueError("Reason must be a short string.")
            if content is not None and (not isinstance(content, str) or len(content.encode()) > self.max_file_bytes):
                raise ValueError("Content must be text within the demo size limit.")
            action_id = uuid.uuid4().hex
            early: Decision | None = None
            normalized = str(path)
            try:
                normalized = _relative(path)
                if not isinstance(operation, str) or operation not in SUPPORTED_OPERATIONS:
                    raise SafetyError("unsupported_operation", "Permanent deletion and shell commands are disabled. Propose quarantine instead.")
                if is_protected(normalized, self.protected_paths):
                    raise SafetyError("protected_path", f"{normalized} is protected; human approval cannot override policy.")
                if operation in {"create", "replace"} and content is None:
                    raise SafetyError("missing_content", "Create/replace requires exact text content for review.")
                if operation == "quarantine" and content is not None:
                    raise SafetyError("unexpected_content", "Quarantine does not accept replacement content.")
                if operation == "create" and expected_hash is not None:
                    raise SafetyError("invalid_hash", "Create requires an absent destination, not a previous hash.")
                if operation != "create":
                    current_hash = self.fingerprint(normalized)
                    if expected_hash is None:
                        expected_hash = current_hash
            except SafetyError as exc:
                early = Decision(action_id, "BLOCK", exc.code, str(exc))
            except OSError as exc:
                early = Decision(action_id, "BLOCK", "filesystem_error", str(exc))
            action = Action(action_id, agent_id, requested_by_slack_id, operation, normalized,
                            expected_hash, content, _sha(content.encode()) if content is not None else None,
                            reason)
            decision = early or self._check(action)
            self._audit("action_evaluated", **self._public(action, decision))
            self._actions[action_id] = action
            self._decisions[action_id] = decision
            return self._public(action, decision)

    def reevaluate_action(self, action_id: str) -> dict:
        """A deferred proposal gets a NEW ID. Old buttons cannot authorize it.

        Preserves the original file hash; changed contents are not auto-approved.
        """
        with self._lock:
            if action_id not in self._actions:
                raise KeyError("Unknown action")
            if action_id in self._superseded or action_id in self._finished:
                raise ValueError("Action is already superseded or finished.")
            if self._decisions[action_id].verdict != "DEFER":
                raise ValueError("Only deferred actions can be reassessed; submit a new proposal otherwise.")
            old = self._actions[action_id]
            new = replace(old, action_id=uuid.uuid4().hex)
            decision = self._check(new)
            self._audit("action_reassessed", previous_action_id=action_id, **self._public(new, decision))
            self._superseded.add(action_id)
            self._actions[new.action_id] = new
            self._decisions[new.action_id] = decision
            return self._public(new, decision)

    def execute_approved_action(self, action_id: str, approver_slack_id: str) -> dict:
        """PRIVILEGED HOST ONLY, called from a verified human approval callback.

        The model never receives this method as a tool. Owner, policy, dependency,
        version, and one-time status are checked again under the same lock used
        for task registration and completion.
        """
        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                return {"action_id": action_id, "executed": False, "verdict": "BLOCK",
                        "reason_code": "unknown_action", "explanation": "Action missing or service restarted; propose again."}
            if approver_slack_id != action.requested_by_slack_id or approver_slack_id not in self.allowed_approver_ids:
                return {**self._public(action, Decision(action_id, "BLOCK", "unauthorized_approver",
                        "Only the human who requested this action can approve it."))}
            if action_id in self._finished:
                return {**self._finished[action_id], "already_executed": self._finished[action_id]["executed"]}
            if action_id in self._superseded:
                return self._public(action, Decision(action_id, "BLOCK", "superseded_action",
                                    "This action was reassessed; use the new proposal and approval button."))
            previous = self._decisions[action_id]
            if previous.verdict != "REVIEW":
                return self._public(action, previous)
            decision = self._check(action)
            self._decisions[action_id] = decision
            if decision.verdict != "REVIEW":
                self._audit("execution_prevented", **self._public(action, decision))
                return self._public(action, decision)
            # Fail closed if the intent cannot be journaled before mutation.
            intent_id = self._audit("execution_authorized", action_id=action_id,
                                    approved_by=approver_slack_id, operation=action.operation,
                                    path=action.path, expected_hash=action.expected_hash,
                                    content_hash=action.content_hash)
            try:
                backup = self._mutate(action)
            except (OSError, SafetyError) as exc:
                result = {**self._public(action, Decision(action_id, "BLOCK", "execution_error", str(exc))),
                          "audit_id": intent_id, "already_executed": False}
                self._finished[action_id] = result  # Do not retry an uncertain mutation automatically.
                self._audit("execution_failed", action_id=action_id, error=str(exc))
                return result
            result = {**self._public(action, Decision(action_id, "ALLOW", "approved_and_executed",
                      f"Approved {action.operation} completed for {action.path}.")),
                      "executed": True, "already_executed": False, "backup_path": backup,
                      "audit_id": intent_id}
            self._finished[action_id] = result
            try:
                result["audit_id"] = self._audit("execution_completed", action_id=action_id,
                                                 operation=action.operation, path=action.path,
                                                 backup_path=backup)
            except OSError as exc:
                result["audit_warning"] = f"Mutation completed, but completion journal write failed: {exc}"
            return dict(result)

    def _mutate(self, action: Action) -> str | None:
        # Private executor method. Do not bind directly as an agent tool.
        target = self._locate(action.path, allow_missing=action.operation == "create")
        if action.operation == "quarantine":
            directory = self.quarantine / action.action_id
            directory.mkdir(mode=0o700)
            backup = directory / "original.bin"
            (directory / "metadata.json").write_text(json.dumps({
                "original_path": action.path, "sha256": action.expected_hash,
                "action_id": action.action_id}), encoding="utf-8")
            os.replace(target, backup)
            return str(backup)
        data = action.content.encode("utf-8")  # validated at proposal time
        if action.operation == "create":
            # Exclusive create: never overwrite an existing output.
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return None
        directory = self.quarantine / action.action_id
        directory.mkdir(mode=0o700)
        backup = directory / "original.bin"
        backup.write_bytes(self._read_bytes(target))
        (directory / "metadata.json").write_text(json.dumps({
            "original_path": action.path, "sha256": action.expected_hash,
            "action_id": action.action_id}), encoding="utf-8")
        staged = directory / "replacement.tmp"
        with staged.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
        return str(backup)

    def status(self) -> dict:
        with self._lock:
            return {"active_tasks": [{"task_id": t.task_id, "owner_slack_id": t.owner_slack_id,
                                      "required_files": list(t.required_files)} for t in self._tasks.values()],
                    "actions": [self._public(a, self._decisions[a.action_id]) for a in self._actions.values()
                                if a.action_id not in self._finished and a.action_id not in self._superseded],
                    "finished": [dict(r) for r in self._finished.values()],
                    "audit_path": str(self.audit_path)}
