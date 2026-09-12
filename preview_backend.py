"""In-memory UI sample only: no AI, disk access, publication, or file protection.

Restarting this process resets every sample task and simulated approval.
Production integrations must implement the contract using their real policy and
execution services; this backend is only for developing Slack interactions.
"""

from dataclasses import replace
from itertools import count
from threading import RLock
from uuid import uuid4

from slack_contract import (
    ApprovalResult, CleanupItem, CleanupView, ReportView, RequestContext,
    StatusView, WorkflowError,
)


SOURCE = "data/source_metrics.csv"
REPORT_INPUT = "working/report_input.csv"
DEBUG = "scratch/debug.log"


class PreviewBackend:
    """Thread-safe sample state, never a filesystem security boundary."""

    preview = True

    def __init__(self):
        self._lock = RLock()
        # Old Slack cards must not refer to new objects after a process restart.
        self._session = uuid4().hex
        self._ids = count(1)
        self._reports = {}
        self._cleanups = {}
        self._commands = {}
        self._approved = set()

    @staticmethod
    def _scope(context):
        if not all((context.user_id, context.team_id, context.channel_id)):
            raise WorkflowError("A user, workspace, and channel are required.")
        return context.team_id, context.channel_id, context.user_id

    def _authorize(self, owner, context):
        if self._scope(owner) != self._scope(context):
            raise WorkflowError("This action belongs to another user or channel.")

    def _command_key(self, kind, context):
        if not context.request_id:
            raise WorkflowError("A request ID is required to avoid duplicate work.")
        return kind, self._scope(context), context.request_id

    def _input_is_held(self, context):
        return any(
            owner.team_id == context.team_id
            and owner.channel_id == context.channel_id
            and report.status in ("running", "awaiting_approval")
            and REPORT_INPUT in report.required_files
            for owner, report in self._reports.values()
        )

    def _decision(self, path, context):
        if path == SOURCE:
            return "BLOCK", "Preview policy: original source data is protected."
        if path == REPORT_INPUT and self._input_is_held(context):
            return "DEFER", "An active sample report needs this input for revisions until approval."
        return "REVIEW", "No active sample dependency; owner approval is required."

    def _refresh(self, batch_id):
        owner, batch = self._cleanups[batch_id]
        items = []
        for item in batch.items:
            if item.verdict == "ALLOW":
                items.append(item)
                continue
            verdict, reason = self._decision(item.path, owner)
            if (verdict, reason) != (item.verdict, item.reason):
                item = replace(item, verdict=verdict, reason=reason,
                               revision=str(int(item.revision) + 1))
            items.append(item)
        batch = replace(batch, items=tuple(items))
        self._cleanups[batch_id] = owner, batch
        return batch

    def start_report(self, context: RequestContext) -> ReportView:
        with self._lock:
            key = self._command_key("report", context)
            if key in self._commands:
                return self._reports[self._commands[key]][1]
            task_id = f"preview-report-{self._session}-{next(self._ids)}"
            report = ReportView(
                task_id=task_id, owner_slack_id=context.user_id, revision="1",
                status="awaiting_approval", required_files=(REPORT_INPUT,),
                title="Sample client report",
                draft_excerpt="Sample draft for testing Slack approval. No source files were read and no AI generated this report.",
            )
            self._reports[task_id] = context, report
            self._commands[key] = task_id
            # Starting a report can invalidate an older cleanup approval card.
            for batch_id, (owner, _) in tuple(self._cleanups.items()):
                if (owner.team_id, owner.channel_id) == (context.team_id, context.channel_id):
                    self._refresh(batch_id)
            return report

    def start_cleanup(self, context: RequestContext) -> CleanupView:
        with self._lock:
            key = self._command_key("cleanup", context)
            if key in self._commands:
                return self._refresh(self._commands[key])
            batch_id = f"preview-cleanup-{self._session}-{next(self._ids)}"
            items = []
            for path in (SOURCE, REPORT_INPUT, DEBUG):
                verdict, reason = self._decision(path, context)
                items.append(CleanupItem(f"preview-action-{self._session}-{next(self._ids)}", path,
                                         verdict, reason, "1"))
            batch = CleanupView(
                batch_id, context.user_id, tuple(items),
                "Preview only: sample decisions and simulated quarantine; no files are read or moved.",
            )
            self._cleanups[batch_id] = context, batch
            self._commands[key] = batch_id
            return batch

    def get_status(self, context: RequestContext) -> StatusView:
        with self._lock:
            scope = self._scope(context)
            reports = tuple(view for owner, view in self._reports.values()
                            if self._scope(owner) == scope)
            batches = tuple(self._refresh(key) for key, (owner, _) in tuple(self._cleanups.items())
                            if self._scope(owner) == scope)
            return StatusView("Preview state for your requests in this channel. No real files or reports were changed.", reports, batches)

    def approve_report(self, task_id, revision, context) -> ApprovalResult:
        with self._lock:
            if task_id not in self._reports:
                raise WorkflowError("This sample report no longer exists. Request a new one.")
            owner, report = self._reports[task_id]
            self._authorize(owner, context)
            key = "report", task_id, revision
            duplicate = key in self._approved
            if not duplicate:
                if report.revision != revision or report.status != "awaiting_approval":
                    raise WorkflowError("This report approval is stale. Run /referee status for current state.")
                report = replace(report, status="completed", revision=str(int(report.revision) + 1))
                self._reports[task_id] = owner, report
                self._approved.add(key)
            followups = () if duplicate else tuple(
                self._refresh(batch_id)
                for batch_id, (batch_owner, _) in tuple(self._cleanups.items())
                if self._scope(batch_owner) == self._scope(owner)
            )
            return ApprovalResult(
                "Preview report approval recorded. No report was published.",
                report=report, followups=followups,
            )

    def approve_cleanup(self, action_id, revision, context) -> ApprovalResult:
        with self._lock:
            match = next(((batch_id, owner) for batch_id, (owner, batch) in self._cleanups.items()
                          if any(item.action_id == action_id for item in batch.items)), None)
            if match is None:
                raise WorkflowError("This sample cleanup action no longer exists. Request a new plan.")
            batch_id, owner = match
            self._authorize(owner, context)
            batch = self._refresh(batch_id)
            item = next(item for item in batch.items if item.action_id == action_id)
            key = "cleanup", action_id, revision
            if key not in self._approved:
                if item.revision != revision:
                    raise WorkflowError("This cleanup approval is stale. Run /referee status for a fresh review.")
                if item.verdict != "REVIEW":
                    raise WorkflowError("Only cleanup items currently awaiting review can be approved.")
                updated = replace(item, verdict="ALLOW", revision=str(int(item.revision) + 1),
                                  reason="Preview: quarantine simulated. No files were moved.")
                batch = replace(batch, items=tuple(updated if other.action_id == action_id else other
                                                   for other in batch.items))
                self._cleanups[batch_id] = owner, batch
                self._approved.add(key)
            return ApprovalResult("Preview cleanup approved; quarantine simulated. No files were moved.",
                                  cleanup=batch)


def create_backend() -> PreviewBackend:
    """Return a fresh, nonpersistent sample backend for the Slack UI."""
    return PreviewBackend()
