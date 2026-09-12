"""Deterministic file-action rules for Agent Referee. No model/API/database dependencies.

Only executor.RefereeService should execute decisions. A Decision is not an
execution permission or capability on its own.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

SUPPORTED_OPERATIONS = frozenset({"create", "replace", "quarantine"})


@dataclass(frozen=True)
class Action:
    action_id: str
    agent_id: str
    requested_by_slack_id: str
    operation: str
    path: str
    expected_hash: str | None
    content: str | None = None
    content_hash: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    owner_slack_id: str
    required_files: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    action_id: str
    verdict: str
    reason_code: str
    explanation: str
    blocking_task_ids: tuple[str, ...] = ()
    suggested_alternative: str | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result["blocking_task_ids"] = list(self.blocking_task_ids)
        result["blocking_task_id"] = next(iter(self.blocking_task_ids), None)
        return result


def is_protected(path: str, protected_paths: Iterable[str]) -> bool:
    """A configured entry protects itself and its subtree; case-insensitive.

    Conservative case folding prevents a simple case-variant bypass on Windows.
    Paths must already be validated and normalized by the executor.
    """
    candidate = path.casefold()
    return any(candidate == p.casefold() or candidate.startswith(p.casefold() + "/")
               for p in protected_paths)


def evaluate(action: Action, active_tasks: Iterable[TaskLease],
             protected_paths: Iterable[str]) -> Decision:
    """Evaluate rules using TRUSTED task state, not text supplied by an LLM."""
    if action.operation not in SUPPORTED_OPERATIONS:
        return Decision(action.action_id, "BLOCK", "unsupported_operation",
                        "Only create, replace, and quarantine are supported; permanent deletion is disabled.")
    if is_protected(action.path, protected_paths):
        return Decision(action.action_id, "BLOCK", "protected_path",
                        f"{action.path} is protected. No approval can override this rule.",
                        suggested_alternative="Keep the source data and work on an approved derived file.")
    blockers = sorted(t.task_id for t in active_tasks
                      if action.path.casefold() in {p.casefold() for p in t.required_files})
    if blockers:
        return Decision(action.action_id, "DEFER", "active_dependency",
                        f"{action.path} is required by unfinished task(s): {', '.join(blockers)}.",
                        tuple(blockers),
                        "Complete and approve the dependent task first, or clean an unrelated file.")
    return Decision(action.action_id, "REVIEW", "approval_required",
                    f"{action.operation.capitalize()} of {action.path} is eligible for owner review; nothing has changed yet.",
                    suggested_alternative="Ask the requesting human to approve this exact action.")
