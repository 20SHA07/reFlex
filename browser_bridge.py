"""Local, disposable demo bridge for the reFlex browser extension.

This hosts the team's real controlled executor. It neither authenticates a Slack
user nor attaches to an arbitrary workspace. Every launch creates fresh fixtures.
Only the locally paired owner can review actions. Reports are deterministic by
default; --live calls backend OpenRouter agents on the same disposable fixtures.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import hmac
import io
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

MAX_BODY_BYTES = 16_384
MAX_CONTEXTS = 32
MAX_REQUESTS = 512
MAX_PENDING_JOBS = 16
MAX_RETAINED_JOBS = 512
ID_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,63}\Z")
PRINCIPAL = {"id": "local-owner", "display_name": "Local demo owner"}
# The Slack rehearsal has one intentional conflict: both agents need this log.
PATHS = ("logs/agent_activity.log",)
CLEANUP_PATHS = PATHS


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
    def __init__(self, root: Path, key: tuple[str, str], *, ai_agents=None,
                 fault_injection: bool = False):
        self.ai_agents = ai_agents
        self.fault_injection = fault_injection
        self.workspace = root / "workspace"
        for folder in ("logs", "reports"):
            (self.workspace / folder).mkdir(parents=True)
        metrics = "day,count\nMonday,12\nTuesday,18\nWednesday,15\n"
        (self.workspace / PATHS[0]).write_text(metrics, encoding="utf-8")
        self.referee = RefereeService(self.workspace, root / "private_state",
                                      allowed_approver_ids={PRINCIPAL["id"]})
        self.state = {
            "mode": "local-demo", "principal": dict(PRINCIPAL),
            "generation": "openrouter" if ai_agents is not None else "sample",
            "context": {"workspace_id": key[0], "channel_id": key[1]},
            "report": None, "cleanup": None, "events": [],
            "message": "Local fixture ready. The report agent will reserve the shared activity log before cleanup requests its deletion.",
        }
        self.requests: dict[str, str] = {}
        self.report_task: str | None = None
        self.report_source_hash: str | None = None
        self.restart_required = False
        if ai_agents is None:
            self.event("info", "Connected to a disposable local fixture. Reports use sample data, without an AI model.")
        else:
            self.event("info", "Connected to OpenRouter agents using disposable sample files. File changes still need your approval.")
            if fault_injection:
                self.event("warning", "Fault injection is enabled: cleanup candidates are fixed demonstration inputs, not an AI selection.")

    def event(self, kind: str, text: str) -> None:
        self.state["events"].append({"id": uuid.uuid4().hex, "kind": kind, "text": text,
                                     "at": datetime.now(timezone.utc).isoformat()})
        self.state["events"] = self.state["events"][-20:]
        self.state["message"] = text

    @staticmethod
    def cleanup_item(decision: dict) -> dict:
        return {
            "id": decision["action_id"],
            "revision": _revision([decision["action_id"], decision["expected_hash"]]),
            "path": decision["path"], "verdict": decision["verdict"],
            "reason": decision["explanation"], "executed": bool(decision["executed"]),
            "dependency_task_id": decision.get("blocking_task_id"),
        }

    def _refresh_cleanup_explanation(self, *, use_model: bool = False) -> None:
        """Explanations are commentary; the executor's item decisions stay intact."""
        cleanup = self.state["cleanup"]
        if cleanup is None:
            return
        items = cleanup["items"]
        pending = [item for item in items if not item["executed"]]
        completed_lines = [f"{item['path']}: {item['verdict']} (already quarantined) — {item['reason']}"
                           for item in items if item["executed"]]
        pending_lines = [f"{item['path']}: {item['verdict']} — {item['reason']}" for item in pending]
        text = "\n".join(pending_lines + completed_lines)
        if not items:
            text = "No cleanup actions were proposed. No files were changed."
        elif not pending:
            text = "No pending cleanup actions remain.\n" + text
        cleanup.update(explanation=text, explanation_model_used=False, explanation_error=False)
        if not use_model or self.ai_agents is None or not pending:
            return
        try:
            from agents import VerifiedDecision
            rows = [VerifiedDecision(path=item["path"], decision=item["verdict"],
                        reason=item["reason"], dependency_task_id=item.get("dependency_task_id"))
                    for item in pending]
            explanation = self.ai_agents.explain_cleanup(rows)
            if not isinstance(explanation.text, str) or not explanation.text.strip():
                raise ValueError("Empty explanation")
            if explanation.model_used and not explanation.error:
                cleanup["explanation"] = "\n".join([explanation.text, *completed_lines])
                cleanup["explanation_model_used"] = True
                self.event("info", "AI explanation added. The referee's individual decisions remain authoritative.")
                return
        except Exception:
            # Provider responses and exceptions can contain sensitive material.
            # Keep the trusted fallback and never echo exception details.
            pass
        cleanup["explanation_error"] = True
        self.event("warning", "AI explanation unavailable. Showing the referee's current rule explanations instead.")

    def start_report(self, text: str) -> None:
        if self.state["report"] is not None:
            raise BridgeError("report_exists", "This demo already has a report. Review it or restart the bridge for a fresh fixture.", 409)
        try:
            self._build_report(text)
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
        self.referee.register_task(task_id, PRINCIPAL["id"], [PATHS[0]])
        self.report_task = task_id
        # Cleanup might have been reviewed first. Replace its now-outdated
        # eligible input review as soon as the report registers a dependency.
        cleanup = self.state["cleanup"]
        if cleanup:
            for index, item in enumerate(cleanup["items"]):
                if item["path"] == PATHS[0] and not item["executed"]:
                    decision = self.referee.evaluate_action("quarantine", PATHS[0],
                        agent_id="sample-cleanup-agent", requested_by_slack_id=PRINCIPAL["id"],
                        reason="Refresh the log-deletion request after the report agent reserves the shared activity log.")
                    cleanup["items"][index] = self.cleanup_item(decision)
                    self.event("info", "Referee deferred the log-deletion request: the report agent still needs the shared activity log.")
            self._refresh_cleanup_explanation()
        source = self.referee.read_text(PATHS[0])
        self.report_source_hash = source["sha256"]
        output_path = f"reports/client_update-{uuid.uuid4().hex}.md"
        if self.ai_agents is None:
            records = list(csv.DictReader(io.StringIO(source["text"])))
            try:
                total = sum(int(row["count"]) for row in records)
            except (KeyError, TypeError, ValueError):
                raise BridgeError("invalid_fixture", "The sample input changed and cannot be summarized.", 409)
            draft = "# Shared activity log report\n\nDeterministic local demo, generated without an AI model.\n\n"
            draft += f"Source log: `{PATHS[0]}`\n\n"
            draft += "\n".join(f"- {row['day']}: {row['count']}" for row in records)
            draft += f"\n\nTotal: {total}.\n"
            if text:
                draft += "\nSelected request context (quoted, not executed):\n\n"
                draft += "\n".join("> " + line for line in text.splitlines()) + "\n"
        else:
            try:
                report = self.ai_agents.run_report(task_id, input_path=PATHS[0],
                    input_text=source["text"], output_path=output_path, request_text=text)
                draft = report.markdown
                if (report.task_id != task_id or report.input_path != PATHS[0]
                        or report.output_path != output_path or not isinstance(draft, str)
                        or not draft.strip() or len(draft) > 16000
                        or len(draft.encode("utf-8")) > self.referee.max_file_bytes):
                    raise ValueError("Agent report does not match the host-assigned task")
            except Exception as exc:
                raise BridgeError("agent_unavailable", "The AI report could not be generated. Check your backend OpenRouter settings and credits.", 502) from exc
        decision = self.referee.evaluate_action("create", output_path,
            agent_id="report-agent" if self.ai_agents is not None else "sample-report-agent",
            requested_by_slack_id=PRINCIPAL["id"], content=draft,
            reason="Publish this exact report only after local owner review.")
        if decision["verdict"] != "REVIEW":
            raise BridgeError("report_blocked", "The executor blocked this report. Restart with a fresh fixture.", 409)
        self.state["report"] = {
            "id": decision["action_id"], "revision": _revision([decision["action_id"], source["sha256"], draft]),
            "status": "awaiting_approval", "input_path": PATHS[0],
            "output_path": output_path, "draft": draft,
        }
        self.event("info", "Report agent drafted a report from the shared activity log. The referee reserves that log until publication is approved.")

    def start_cleanup(self) -> None:
        previous = {item["path"]: item for item in (self.state["cleanup"] or {}).get("items", [])}
        paths = list(CLEANUP_PATHS)
        hashes = {}
        reason = "Cleanup agent requested deletion; the referee uses recoverable quarantine for the disposable demo."
        proposal_source = "sample"
        if self.ai_agents is not None:
            # Capture versions before the model considers the host-scoped paths.
            # Never register model-chosen identities or accept arbitrary paths.
            inventory = [path for path in CLEANUP_PATHS if not previous.get(path, {}).get("executed")]
            hashes = {path: self.referee.fingerprint(path) for path in inventory}
            try:
                cleanup_task_id = uuid.uuid4().hex
                proposal = self.ai_agents.run_cleanup(cleanup_task_id, inventory,
                                                       fault_injection=self.fault_injection)
                paths = proposal.paths
                reason = proposal.reason
                if (proposal.task_id != cleanup_task_id
                        or not isinstance(paths, (tuple, list)) or len(paths) > len(inventory)
                        or any(not isinstance(path, str) or path not in inventory for path in paths)
                        or len(set(paths)) != len(paths)
                        or not isinstance(reason, str) or not reason.strip() or len(reason) > 1000):
                    raise ValueError("Invalid cleanup proposal")
                proposal_source = "fault_injection" if self.fault_injection else "model"
            except Exception as exc:
                # No proposal has been evaluated or stored when generation fails.
                raise BridgeError("agent_unavailable", "The AI cleanup plan could not be generated. No new cleanup actions were submitted. Check your backend OpenRouter settings and try again.", 502) from exc
        items = []
        for path in paths:
            if previous.get(path, {}).get("executed"):
                items.append(previous[path])
                continue
            decision = self.referee.evaluate_action("quarantine", path,
                agent_id="cleanup-agent" if self.ai_agents is not None else "sample-cleanup-agent",
                requested_by_slack_id=PRINCIPAL["id"], expected_hash=hashes.get(path), reason=reason)
            items.append(self.cleanup_item(decision))
        if self.ai_agents is not None:
            items.extend(item for path, item in previous.items() if item["executed"] and path not in paths)
        self.state["cleanup"] = {"id": uuid.uuid4().hex, "items": items,
                                  "proposal_source": proposal_source}
        self.event("info", "Cleanup agent requested log deletion. The referee checks whether another agent still depends on each file."
                   if paths else "No new cleanup candidates were proposed. No files were changed.")
        if self.fault_injection:
            self.event("warning", "Cleanup used fixed fault-injection candidates to demonstrate referee protection; the model did not choose these paths.")
        self._refresh_cleanup_explanation(use_model=True)

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
            if self.referee.fingerprint(PATHS[0]) != self.report_source_hash:
                raise BridgeError("stale_review", "The shared activity log changed. Restart the disposable demo to review a fresh report.", 409)
        except SafetyError:
            raise BridgeError("stale_review", "The shared activity log is unavailable. Restart the disposable demo.", 409)
        result = self.referee.execute_approved_action(report["id"], PRINCIPAL["id"])
        if not result["executed"]:
            raise BridgeError("execution_blocked", "The executor blocked publication. The shared activity log remains reserved.", 409)
        published = self.referee.read_text(report["output_path"])
        if published["text"] != report["draft"]:
            raise BridgeError("publication_changed", "Published output differs from the reviewed report. The shared activity log remains reserved.", 409)
        self.referee.complete_task(self.report_task, PRINCIPAL["id"])
        report["status"] = "completed"
        self.event("success", f"Approved report published to {report['output_path']}. The shared activity log is released for a fresh cleanup review.")
        if result.get("audit_warning"):
            self.event("warning", "The report was published, but its completion journal could not be written. Inspect the local audit files.")
        cleanup = self.state["cleanup"]
        if cleanup:
            for index, item in enumerate(cleanup["items"]):
                if item["verdict"] == "DEFER":
                    fresh = self.referee.reevaluate_action(item["id"])
                    cleanup["items"][index] = self.cleanup_item(fresh)
                    self.event("info", f"{item['path']} has a fresh {fresh['verdict']} decision. Any eligible cleanup needs a new approval.")
            self._refresh_cleanup_explanation()

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
        item.update(self.cleanup_item(result))
        self._refresh_cleanup_explanation()
        if not result["executed"]:
            self.event("warning", f"Cleanup prevented for {item['path']}. Request a fresh review.")
            raise BridgeError("stale_review", "The executor prevented cleanup. Refresh and request a fresh review.", 409)
        self.event("success", f"Approved cleanup moved {item['path']} to quarantine. No permanent deletion.")
        if result.get("audit_warning"):
            self.event("warning", "Cleanup completed, but its completion journal could not be written. Inspect the local audit files.")


class BrowserBridge:
    def __init__(self, *, ai_agents=None, fault_injection: bool = False):
        self.ai_agents = ai_agents
        self.fault_injection = fault_injection
        self.generation = "openrouter" if ai_agents is not None else "sample"
        self.root = Path(tempfile.mkdtemp(prefix="reflex_browser_demo_"))
        self.root.chmod(0o700)
        self.token = secrets.token_urlsafe(32)
        self.token_path = self.root / "pairing-token.txt"
        with self.token_path.open("x", encoding="utf-8") as handle:
            os.chmod(self.token_path, 0o600)
            handle.write(self.token + "\n")
        self.sessions: dict[tuple[str, str], DemoSession] = {}
        self.lock = threading.RLock()
        # Model calls run inside the existing serialized workflow boundary.
        # Polling has a separate lock, so HTTP requests never wait on a model.
        self.jobs_lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.job_requests: dict[tuple[tuple[str, str], str], str] = {}
        self.pending_jobs = 0

    def authenticated(self, header: str | None) -> bool:
        if not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        candidate = header[7:]
        return len(candidate) <= 256 and hmac.compare_digest(candidate.encode(), self.token.encode())

    @staticmethod
    def _dispatch_input(body: object) -> tuple:
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
        return operation, key, request_id, text, fingerprint

    def submit(self, body: object) -> dict:
        """Queue one bounded job; repeated requests reuse the same pending job."""
        operation, key, request_id, text, fingerprint = self._dispatch_input(body)
        request_key = (key, request_id)
        with self.jobs_lock:
            existing_id = self.job_requests.get(request_key)
            if existing_id is not None:
                existing = self.jobs[existing_id]
                if existing["fingerprint"] != fingerprint:
                    raise BridgeError("request_conflict", "This request identifier was already used for different input.", 409)
                return {"ok": True, "job_id": existing_id}
            if self.pending_jobs >= MAX_PENDING_JOBS:
                raise BridgeError("job_limit", "The local agents are busy. Wait for a current operation to finish, then retry.", 429)
            while len(self.jobs) >= MAX_RETAINED_JOBS:
                finished_id = next((job_id for job_id, job in self.jobs.items() if job["result"] is not None), None)
                if finished_id is None:
                    raise BridgeError("job_limit", "The local agents are busy. Wait for a current operation to finish, then retry.", 429)
                finished = self.jobs.pop(finished_id)
                self.job_requests.pop(finished["request_key"], None)
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"request_key": request_key, "fingerprint": fingerprint,
                                 "status": 202, "result": None}
            self.job_requests[request_key] = job_id
            self.pending_jobs += 1
            # Copy before returning: subsequent caller mutations cannot change a
            # queued approval target or model request.
            worker = threading.Thread(target=self._run_job, args=(job_id, copy.deepcopy(body)), daemon=True)
            try:
                worker.start()
            except RuntimeError as exc:
                self.jobs.pop(job_id)
                self.job_requests.pop(request_key)
                self.pending_jobs -= 1
                raise BridgeError("job_unavailable", "The local worker could not start. Try again.", 503) from exc
            return {"ok": True, "job_id": job_id}

    def _run_job(self, job_id: str, body: dict) -> None:
        try:
            result = self.dispatch(body)
            status = 200
        except BridgeError as exc:
            status = exc.status
            result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except Exception:
            # Never return raw provider/OS exceptions, request headers, or keys.
            status = 503
            result = {"ok": False, "error": {"code": "demo_unavailable",
                "message": "The local workflow could not finish. Refresh its status before retrying; check the backend configuration."}}
        with self.jobs_lock:
            self.jobs[job_id].update(status=status, result=result)
            self.pending_jobs -= 1

    def job_status(self, job_id: str) -> tuple[int, dict]:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise BridgeError("unknown_job", "This operation is no longer available. Refresh the current workflow state.", 404)
            if job["result"] is None:
                return 202, {"ok": True, "job_id": job_id}
            return job["status"], copy.deepcopy(job["result"])

    def dispatch(self, body: object) -> dict:
        operation, key, request_id, text, fingerprint = self._dispatch_input(body)
        with self.lock:
            session = self.sessions.get(key)
            if session is None:
                if len(self.sessions) >= MAX_CONTEXTS:
                    raise BridgeError("context_limit", "Restart this demo bridge before adding more channels.", 429)
                session = DemoSession(self.root / uuid.uuid4().hex, key,
                                      ai_agents=self.ai_agents, fault_injection=self.fault_injection)
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
                    session.start_cleanup()
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
            if (self.path not in {"/v1/session", "/v1/dispatch"}
                    and not re.fullmatch(r"/v1/jobs/[0-9a-f]{32}", self.path)):
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
            if self.path == "/v1/session":
                self._respond(200, {"ok": True, "principal": dict(PRINCIPAL),
                                   "mode": "local-demo", "generation": self.server.bridge.generation})
            elif re.fullmatch(r"/v1/jobs/[0-9a-f]{32}", self.path):
                status, result = self.server.bridge.job_status(self.path.rsplit("/", 1)[1])
                self._respond(status, result)
            else:
                raise BridgeError("not_found", "Unknown bridge route.", 404)
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
            if self.server.bridge.ai_agents is not None:
                self._respond(202, self.server.bridge.submit(body))
            else:
                self._respond(200, self.server.bridge.dispatch(body))
        except BridgeError as exc:
            self._error(exc)
        except (OSError, ValueError, KeyError):
            self._error(BridgeError("demo_unavailable", "The local fixture is unavailable. Restart the bridge for a fresh demo.", 503))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-id", help="ID shown in chrome://extensions; permits only that extension Origin.")
    parser.add_argument("--live", action="store_true", help="Use backend OpenRouter agents on the disposable sample files.")
    parser.add_argument("--fault-injection", action="store_true", help="With --live, use fixed cleanup candidates to demonstrate blocked/deferred actions.")
    args = parser.parse_args()
    if args.fault_injection and not args.live:
        parser.error("--fault-injection requires --live. The default sample demo already uses fixed cleanup candidates.")
    ai_agents = None
    if args.live:
        try:
            from agents import OpenRouterClient, ReFlexAgents
            ai_agents = ReFlexAgents(OpenRouterClient.from_env())
        except Exception:
            parser.error("Live mode needs a valid OPENROUTER_API_KEY and OPENROUTER_MODEL in this terminal. Check the setup guide; do not paste the key into extension settings.")
    bridge = BrowserBridge(ai_agents=ai_agents, fault_injection=args.fault_injection)
    server = BridgeServer(bridge, extension_id=args.extension_id)
    print("reFlex local demo bridge: http://127.0.0.1:8765", flush=True)
    print(f"Pairing token file (private; copy its contents into extension settings): {bridge.token_path}", flush=True)
    print(f"Disposable fixtures and audit files: {bridge.root}", flush=True)
    print("Local demo owner only. Slack context is not identity.", flush=True)
    if args.live:
        print("OpenRouter agents enabled. Requests use account credits; only disposable sample files are available.", flush=True)
        if args.fault_injection:
            print("Fault injection: cleanup candidates are fixed demonstration inputs, not an AI selection.", flush=True)
    else:
        print("Sample reports do not use an AI model. Add --live to enable backend OpenRouter agents.", flush=True)
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
