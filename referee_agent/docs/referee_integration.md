# Agent Referee Core Integration

Drop-in Python modules for the Slack hackathon demo. No API keys, model calls,
GPU, Firebase, Slack SDK, or third-party Python packages are required to test
this part. Use Python 3.10+; locally tested on Python 3.13.5.

## Quick start

Extract these files into the new project's root (or try them in a separate
folder before copying only the two production modules):

```text
referee.py
executor.py
demo.py
tests/test_referee.py
docs/referee_integration.md
```

From that folder:

```bash
python -m unittest discover -s tests -v
python demo.py
```

On Windows, `py` can replace `python`. On macOS/Linux, `python3` can replace it.
The demo creates a fresh temporary project on every execution. It does not
operate on your repository's real data. Demo output is a deterministic injected
safety test, not a live Slack or model run.

## Expected behavior

| Proposed action | Verdict | Actual mutation |
| --- | --- | --- |
| Quarantine `data/source_metrics.csv` | BLOCK / protected_path | None, even if a human tries to approve |
| Quarantine `working/report_input.csv` during report task | DEFER / active_dependency | None |
| Quarantine `scratch/debug.log` | REVIEW / approval_required | None until owner approves |
| Authorized approval of the eligible log cleanup | ALLOW / approved_and_executed | Move to private quarantine |
| Reassess report input after owner completes report | REVIEW with a NEW action ID | None until a fresh approval |

All actions go through the same rules. Agent-written explanations cannot grant
permissions. A model must request `quarantine`; `delete`, shell commands, and
whole-directory operations are unsupported and blocked.

## Integration responsibilities

- `referee.py` contains deterministic policy decisions.
- `executor.py` owns trusted execution, approvals, version checks, quarantine, and audit logging.
- The application backend owns one shared `RefereeService` instance, workflow orchestration, and verified identity context.
- The Slack layer owns commands, cards, buttons, and verified callback identities.
- Model workers may plan, draft, and propose actions, but never authorize execution.
- The test suite should run as part of the project's main validation workflow.

These modules return ordinary dictionaries so they can map into the project's shared schema without requiring a competing schema module.

## Initialize ONCE in the application process

Create the fixture folders first. Put private state outside `demo_workspace`.
The IDs below are examples: replace them with actual verified Slack user IDs
in the application's configuration, not model-supplied values.

```python
from executor import RefereeService

referee = RefereeService(
    workspace="demo_workspace",
    state_dir=".referee_private",  # sibling, NOT inside demo_workspace
    allowed_approver_ids={"U_REPORT_OWNER", "U_CLEANUP_OWNER"},
    protected_paths=("data",),  # protects data/ and everything beneath it
)
```

Add `.referee_private/`, `.env`, and credentials to `.gitignore`. Do not pass
these folders or secrets to agents.

## Report workflow

Register dependencies before a worker reads the input, and keep the lease
active while the draft awaits its owner's approval.

```python
referee.register_task(
    task_id="report-001",
    owner_slack_id="U_REPORT_OWNER",  # from verified Slack command
    required_files=["working/report_input.csv"],
)
input_snapshot = referee.read_text("working/report_input.csv")
# The report agent generates a report draft from input_snapshot["text"].
# Show that EXACT draft in Slack; don't allow the worker to write it directly.
draft = "# Client update\nThis is an example draft, not a model result.\n"
report_proposal = referee.evaluate_action(
    "create", "reports/client_update.md",
    agent_id="report-agent",             # assigned by application
    requested_by_slack_id="U_REPORT_OWNER",
    content=draft,
)
# Save report_proposal["action_id"] with the report's task record.
# Post approval button after checking report_proposal["verdict"] == "REVIEW".
```

When the report owner clicks the button, The Slack handler acknowledges promptly;
the application backend executes the approved create and ONLY THEN releases the input lease:

```python
outcome = referee.execute_approved_action(
    report_proposal["action_id"],
    verified_slack_user_id,  # from the verified interaction, never an LLM
)
if outcome["executed"]:
    referee.complete_task("report-001", verified_slack_user_id)
```

The host is responsible for deciding that the report is ready to publish.
`complete_task` verifies the task owner; it does not independently judge the
quality or scientific correctness of the report. Never expose it as an agent
completion shortcut.

## Cleanup workflow

The cleanup agent returns proposed operations. The application backend binds the authenticated requester
and agent identity in a wrapper; do not splat untrusted model JSON into privileged
keyword arguments.

```python
proposal = referee.evaluate_action(
    "quarantine",
    "working/report_input.csv",        # untrusted path is validated
    agent_id="cleanup-agent",          # controlled by application
    requested_by_slack_id="U_CLEANUP_OWNER",  # verified Slack requester
    reason="Cleanup of working files",
)
```

Typical return value:

```json
{
  "action_id": "server-generated-id",
  "verdict": "DEFER",
  "reason_code": "active_dependency",
  "explanation": "working/report_input.csv is required by unfinished task(s): report-001.",
  "blocking_task_ids": ["report-001"],
  "blocking_task_id": "report-001",
  "operation": "quarantine",
  "path": "working/report_input.csv",
  "executed": false
}
```

The real response also includes requester/agent IDs, expected content hash,
proposed-content hash, and a suggested alternative. The immutable action stored
inside the service is authoritative; changing the returned dict cannot grant
permission or change the operation.

For eligible actions only, put the action ID in the Slack approval button. Do
NOT put mutable paths/operations in the execution call:

```python
outcome = referee.execute_approved_action(action_id, verified_slack_user_id)
```

This verifies ownership, recomputes rules/active dependencies/content hash, and
then executes under a single lock. It returns a JSON-ready outcome including
`executed`, `reason_code`, `already_executed`, `backup_path`, and `audit_id`.
For a repeated click, show "already completed" when `already_executed` is true.
Only the requester may approve; a second configured user is not an override.

## Reassess, do not auto-approve

After report completion:

```python
fresh = referee.reevaluate_action(deferred_action_id)
# If fresh["verdict"] == "REVIEW", post a NEW approval button using its NEW ID.
# The old action ID is now superseded and cannot be executed.
```

If file contents changed in the meantime, the old expected hash is preserved
and reassessment BLOCKS rather than silently authorizing a different version.
Submit a new proposal and review it separately to accept changed content.

## Read-time hashes

For agents that read a file before suggesting a modification, carry the verified
hash from `read_text` into `evaluate_action(expected_hash=...)`. If omitted,
the proposal captures the version that exists when it is submitted for review.
In either case execution checks the stored hash again.

## Shared state and optional SQLite/Firebase

For this two-minute demo, use one service instance in one backend process as
the enforcement registry. The application may mirror its JSON-ready results into
SQLite/Firestore for UI/history. Register and complete tasks through this
service too; writing a task only to a separate database will NOT protect it.
Do not create a new service instance per Slack command.

The JSONL journal is durable evidence, NOT a crash-recovery database. Pending
actions and task leases are in memory. After a process restart, old action IDs
are invalid; restore verified task state before accepting new work, or start
with a fresh demo fixture. Do not run multiple independent backend workers.
Production distributed persistence/transactions are outside this build.

## Security and scope

This is a controlled tool boundary, not an OS sandbox. All participating agents
must use the wrapper. An external process or agent with direct filesystem/shell
access can bypass it. The lock coordinates only this service's threads, not
other processes. Rechecking paths/hashes is not a general defense against an
adversary concurrently replacing filesystem components outside this service.

Path traversal, symlinks, directory junctions where supported, hardlinks,
unsupported operations, stale content, and unauthorized approvals are rejected.
Only individual regular files are supported. New parent directories must exist.
Mutations are size-limited. Existing files are quarantined/backed up, not
permanently deleted. File contents/credentials are not written into audit events.
Protected paths are configured by humans; undisclosed dependencies and unknown
critical files are not automatically inferred. Source data is readable in this
disposable fixture: do not put secrets in it.

Execution attempts are journaled before mutation; journal failure prevents the
mutation. A completion-journal failure after a successful mutation is returned
as a warning, not represented as a harmless retry. Exactly-once behavior is
provided only for repeated action IDs in the same process lifetime, not across
crashes. Review I/O errors before retrying.

## Validation

The included suite checks 30 cases, including protected paths, active leases,
authorization, duplicate/concurrent clicks, stale content, read-time hashes,
new dependencies after review, fresh approval after deferral, path traversal,
symlinks/hardlinks, create/replace backups, and audit failure.

Local result: 30 tests passed on Python 3.13.5 (Linux), plus the fixture demo.
No Slack, OpenRouter, Firebase, Windows runtime, or OS isolation test was performed
here. Run the tests on the integration machine before the video.

Security rationale: OWASP recommends least-privilege agent tools, independent
authorization, human oversight for high-impact actions, and not trusting model
output as permission:
https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
