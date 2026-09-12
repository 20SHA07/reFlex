"""UI views and adapter contract. Map your team's schemas to these at the boundary.

The backend owns authorization, idempotency, current dependency checks, file
version checks and mutations. A button value is a reference, never permission.
"""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    team_id: str
    channel_id: str
    request_id: str


@dataclass(frozen=True)
class ReportView:
    task_id: str
    owner_slack_id: str
    revision: str
    status: Literal["running", "awaiting_approval", "completed", "failed"]
    required_files: tuple[str, ...] = ()
    draft_excerpt: str = ""
    title: str = "Client report"


@dataclass(frozen=True)
class CleanupItem:
    action_id: str
    path: str
    verdict: Literal["BLOCK", "DEFER", "REVIEW", "ALLOW"]
    reason: str
    revision: str


@dataclass(frozen=True)
class CleanupView:
    batch_id: str
    owner_slack_id: str
    items: tuple[CleanupItem, ...]
    plan: str = ""


@dataclass(frozen=True)
class StatusView:
    message: str
    reports: tuple[ReportView, ...] = ()
    cleanups: tuple[CleanupView, ...] = ()


@dataclass(frozen=True)
class ApprovalResult:
    message: str
    # False: show only the requester a rejection; leave the shared card intact.
    accepted: bool = True
    report: ReportView | None = None
    cleanup: CleanupView | None = None
    # Fresh cleanup cards after report publication. These never auto-execute.
    followups: tuple[CleanupView, ...] = ()
    # Populate only when the backend actually has an audit record.
    audit_id: str | None = None


class WorkflowError(Exception):
    """Expected failure with a message safe to show the requester in Slack."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


class WorkflowBackend(Protocol):
    # Must be True for every backend that simulates decisions or execution.
    preview: bool

    def start_report(self, context: RequestContext) -> ReportView: ...

    def start_cleanup(self, context: RequestContext) -> CleanupView: ...

    def get_status(self, context: RequestContext) -> StatusView: ...

    def approve_report(
        self, task_id: str, revision: str, context: RequestContext
    ) -> ApprovalResult: ...

    def approve_cleanup(
        self, action_id: str, revision: str, context: RequestContext
    ) -> ApprovalResult: ...
