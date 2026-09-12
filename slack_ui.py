"""Person 2's Slack boundary: rendering, routing and acknowledgements only.

No model calls, policy decisions or filesystem operations happen here.
Run through Bolt's SocketModeHandler so Slack authenticates the event channel.
"""

import hashlib
import html
import json
import logging
import re
from typing import Any

from slack_contract import (
    ApprovalResult, CleanupView, ReportView, RequestContext, StatusView,
    WorkflowBackend, WorkflowError,
)

PREVIEW = "UI PREVIEW: sample state only. No AI calls or file operations."
HELP = "Use /referee report, /referee cleanup, or /referee status."
APPROVE_REPORT = "referee_approve_report"
APPROVE_CLEANUP = "referee_approve_cleanup"
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,63}$")


def _clip(value: str, limit: int = 2800) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _plain(text: str) -> dict:
    # User/model text remains literal, including Slack mention syntax.
    return {"type": "plain_text", "text": _clip(text) or " ", "emoji": False}


def _section(text: str) -> dict:
    return {"type": "section", "text": _plain(text)}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [_plain(text)]}


def _start(title: str, preview: bool) -> list[dict]:
    blocks = [{"type": "header", "text": _plain(_clip(title, 150))}]
    if preview:
        blocks.append(_context(PREVIEW))
    return blocks


def _reference(identifier: str, revision: str) -> str:
    if not all(isinstance(v, str) and 0 < len(v) <= 200 for v in (identifier, revision)):
        raise ValueError("Backend IDs and revisions must be nonempty strings up to 200 characters")
    value = json.dumps({"id": identifier, "revision": revision}, separators=(",", ":"), ensure_ascii=False)
    if len(value) > 2000:
        raise ValueError("Approval reference exceeds Slack's button value limit")
    return value


def _button(label: str, action_id: str, identifier: str, revision: str, question: str) -> dict:
    return {
        "type": "actions",
        "elements": [{
            "type": "button", "action_id": action_id,
            "text": _plain(_clip(label, 75)), "style": "primary",
            "value": _reference(identifier, revision),
            "confirm": {
                "title": _plain("Confirm approval"),
                "text": _plain(_clip(question, 300)),
                "confirm": _plain("Approve"), "deny": _plain("Go back"),
            },
        }],
    }


def report_card(view: ReportView, preview: bool = False) -> dict:
    labels = {"running": "Preparing draft", "awaiting_approval": "Awaiting owner approval",
              "completed": "Published", "failed": "Could not complete"}
    status = labels[view.status]
    if preview and view.status == "completed":
        status = "Approval simulated"
    blocks = _start("Report Agent · " + view.title, preview)
    blocks.append(_section(f"Status: {status}\nOwner: {view.owner_slack_id}"))
    if view.required_files:
        blocks.append(_section("Required files\n" + "\n".join(view.required_files)))
    if view.draft_excerpt:
        blocks.append(_section("Report excerpt\n" + view.draft_excerpt))
    blocks.append(_context(f"Task: {view.task_id} · Draft version: {view.revision}"))
    if view.status == "awaiting_approval":
        question = "Approve this draft for publication? The backend will check the current version and ownership."
        if preview:
            question = "Simulate approval of this sample draft? No report file will be published."
        blocks.append(_button("Approve report", APPROVE_REPORT, view.task_id, view.revision, question))
    return {"text": ("[UI preview] " if preview else "") + f"Report Agent: {status}", "blocks": blocks}


def cleanup_card(view: CleanupView, preview: bool = False) -> dict:
    if len(view.items) > 12:
        raise ValueError("Return at most 12 cleanup items per view; split larger batches in the backend")
    labels = {"BLOCK": "BLOCKED", "DEFER": "DEFERRED", "REVIEW": "NEEDS APPROVAL", "ALLOW": "PERMITTED"}
    blocks = _start("Referee · Cleanup review", preview)
    blocks.append(_section(f"Requested by: {view.owner_slack_id}"))
    if view.plan:
        blocks.append(_section("Proposed plan\n" + view.plan))
    for item in view.items:
        blocks.append(_section(f"{labels[item.verdict]} · {item.path}\n{item.reason}"))
        if item.verdict == "REVIEW":
            question = f"Move {item.path} to quarantine if it is still eligible?"
            if preview:
                question = f"Simulate approval for {item.path}? No file will move."
            blocks.append(_button("Approve cleanup", APPROVE_CLEANUP, item.action_id, item.revision, question))
    if not view.items:
        blocks.append(_section("No cleanup candidates."))
    blocks.append(_context(f"Review: {view.batch_id}"))
    return {"text": ("[UI preview] " if preview else "") + "Referee: cleanup review", "blocks": blocks}


def status_card(view: StatusView, preview: bool = False) -> dict:
    blocks = _start("Agent Referee · Status", preview)
    blocks.append(_section(view.message))
    if view.reports:
        lines = [f"{v.task_id}: {v.status}" for v in view.reports]
        blocks.append(_section("Your reports\n" + "\n".join(lines)))
    if view.cleanups:
        lines = [f"{v.batch_id}: " + ", ".join(f"{i.path} [{i.verdict}]" for i in v.items) for v in view.cleanups]
        blocks.append(_section("Your cleanup reviews\n" + "\n".join(lines)))
    blocks.append(_context(HELP))
    # A stale approval must have a recovery path without creating duplicate work.
    # One latest report and cleanup fit under Slack's 50-block message limit.
    if view.reports:
        pending = [report for report in view.reports if report.status == "awaiting_approval"]
        current_report = pending[-1] if pending else view.reports[-1]
        blocks.append({"type": "divider"})
        blocks.extend(report_card(current_report, preview)["blocks"])
    if view.cleanups:
        blocks.append({"type": "divider"})
        blocks.extend(cleanup_card(view.cleanups[-1], preview)["blocks"])
    return {"text": ("[UI preview] " if preview else "") + "Agent Referee status", "blocks": blocks}


def outcome_card(result: ApprovalResult, preview: bool = False) -> dict:
    blocks = _start("Referee · Outcome", preview)
    blocks.append(_section(result.message))
    if result.audit_id:
        blocks.append(_context("Audit record: " + result.audit_id))
    return {"text": ("[UI preview] " if preview else "") + html.escape(_clip(result.message, 250), quote=False), "blocks": blocks}


class SlackUI:
    def __init__(self, backend: WorkflowBackend, expected_team_id: str | None = None):
        self.backend = backend
        self.preview = backend.preview
        if type(self.preview) is not bool:
            raise ValueError("backend.preview must explicitly be True or False")
        self.expected_team_id = expected_team_id or None
        self.logger = logging.getLogger(__name__)

    def _context_from(self, body: dict, interactive: bool = False) -> RequestContext:
        if interactive:
            user_id = body.get("user", {}).get("id", "")
            team_id = body.get("team", {}).get("id", "")
            channel_id = body.get("channel", {}).get("id", "")
        else:
            user_id, team_id, channel_id = (body.get(k, "") for k in ("user_id", "team_id", "channel_id"))
        if not all(isinstance(v, str) and ID_PATTERN.fullmatch(v) for v in (user_id, team_id, channel_id)):
            raise WorkflowError("This request is missing its Slack user, workspace, or channel. Try again in the demo channel.")
        if self.expected_team_id and team_id != self.expected_team_id:
            raise WorkflowError("This app is configured for a different workspace.")
        trigger_id = body.get("trigger_id")
        if not isinstance(trigger_id, str) or not trigger_id:
            raise WorkflowError("This request has expired. Run the command or click the current card again.")
        # Stable across a retried Slack delivery. Scope it to authenticated context.
        source = json.dumps([team_id, channel_id, user_id, trigger_id], separators=(",", ":"))
        request_id = hashlib.sha256(source.encode()).hexdigest()
        return RequestContext(user_id, team_id, channel_id, request_id)

    def _error(self, respond, error: Exception) -> None:
        if isinstance(error, WorkflowError):
            message = error.public_message
        else:
            # Never dump tokens, raw request bodies, backend exception text, or drafts.
            self.logger.error("Slack workflow failed (%s)", type(error).__name__)
            message = "The workflow could not finish. Check /referee status before retrying an approval."
        try:
            respond(response_type="ephemeral", replace_original=False,
                    text=html.escape(message, quote=False), blocks=[_section(message)])
        except Exception as notification_error:
            self.logger.error("Could not deliver requester error (%s)", type(notification_error).__name__)

    def command(self, ack, body, client, respond) -> None:
        ack()  # Always before network calls, backend calls, and parsing.
        try:
            context = self._context_from(body)
            command = body.get("text", "").strip().lower()
            if command == "status":
                card = status_card(self.backend.get_status(context), self.preview)
                respond(response_type="ephemeral", replace_original=False, **card)
                return
            if command not in ("report", "cleanup"):
                respond(response_type="ephemeral", replace_original=False, text=HELP)
                return
            stage = "Report Agent: preparing draft…" if command == "report" else "Cleanup Agent: reviewing candidates…"
            loading = {"text": stage, "blocks": _start(stage, self.preview)}
            pending = client.chat_postMessage(channel=context.channel_id, **loading)
            timestamp = pending["ts"]
            try:
                view = self.backend.start_report(context) if command == "report" else self.backend.start_cleanup(context)
                card = report_card(view, self.preview) if command == "report" else cleanup_card(view, self.preview)
            except Exception:
                # No stale spinner, and no claim that the backend rolled anything back.
                failed = "This request could not finish. Use /referee status to check its current state."
                client.chat_update(channel=context.channel_id, ts=timestamp, text=failed,
                                   blocks=_start("Agent Referee · Request interrupted", self.preview) + [_section(failed)])
                raise
            client.chat_update(channel=context.channel_id, ts=timestamp, **card)
        except Exception as error:
            self._error(respond, error)

    def action(self, ack, body, client, respond) -> None:
        ack()
        try:
            context = self._context_from(body, interactive=True)
            actions = body.get("actions", [])
            if len(actions) != 1:
                raise WorkflowError("This approval is not valid. Request a fresh card.")
            action = actions[0]
            action_type = action.get("action_id")
            if action_type not in (APPROVE_REPORT, APPROVE_CLEANUP):
                raise WorkflowError("This action is not supported.")
            raw = action.get("value", "")
            if not isinstance(raw, str) or len(raw) > 2000:
                raise WorkflowError("This approval is not valid. Request a fresh card.")
            try:
                value = json.loads(raw)
                if not isinstance(value, dict) or set(value) != {"id", "revision"}:
                    raise ValueError()
                _reference(value["id"], value["revision"])
            except (ValueError, TypeError):
                raise WorkflowError("This approval is not valid. Request a fresh card.") from None
            timestamp = body.get("message", {}).get("ts", "")
            if not isinstance(timestamp, str) or not re.fullmatch(r"\d+\.\d+", timestamp):
                raise WorkflowError("This card is no longer available. Request a fresh one.")
            # These values are references only. The backend must re-authorize and
            # re-evaluate current policy, dependencies and file versions atomically.
            if action_type == APPROVE_REPORT:
                result = self.backend.approve_report(value["id"], value["revision"], context)
            else:
                result = self.backend.approve_cleanup(value["id"], value["revision"], context)
            if not result.accepted:
                respond(response_type="ephemeral", replace_original=False, **outcome_card(result, self.preview))
                return
            updated = None
            if action_type == APPROVE_REPORT and result.report is not None:
                updated = report_card(result.report, self.preview)
            elif action_type == APPROVE_CLEANUP and result.cleanup is not None:
                updated = cleanup_card(result.cleanup, self.preview)
            if updated:
                if body.get("container", {}).get("is_ephemeral") is True:
                    respond(response_type="ephemeral", replace_original=True, **updated)
                else:
                    client.chat_update(channel=context.channel_id, ts=timestamp, **updated)
            # Status cards are private; keep their outcomes private too. Slack
            # cannot update an ephemeral message using chat.update.
            if body.get("container", {}).get("is_ephemeral") is True:
                respond(response_type="ephemeral", replace_original=False, **outcome_card(result, self.preview))
                for followup in result.followups:
                    respond(response_type="ephemeral", replace_original=False, **cleanup_card(followup, self.preview))
            else:
                client.chat_postMessage(channel=context.channel_id, thread_ts=timestamp,
                                        **outcome_card(result, self.preview))
                for followup in result.followups:
                    client.chat_postMessage(channel=context.channel_id, thread_ts=timestamp,
                                            **cleanup_card(followup, self.preview))
        except Exception as error:
            self._error(respond, error)


def register_handlers(app: Any, backend: WorkflowBackend, *, expected_team_id: str | None = None) -> SlackUI:
    """Register on the integration lead's existing Bolt App. Call exactly once."""
    ui = SlackUI(backend, expected_team_id)

    def on_command(ack, body, client, respond):
        ui.command(ack, body, client, respond)

    def on_action(ack, body, client, respond):
        ui.action(ack, body, client, respond)

    app.command("/referee")(on_command)
    app.action(APPROVE_REPORT)(on_action)
    app.action(APPROVE_CLEANUP)(on_action)
    return ui


def create_app(backend: WorkflowBackend, *, bot_token: str, expected_team_id: str | None = None):
    """Create a Bolt app for the provided Socket Mode runner."""
    from slack_bolt import App
    app = App(token=bot_token)
    register_handlers(app, backend, expected_team_id=expected_team_id)
    return app
