# reFlex: Agent Referee for Slack

This branch contains Person 2's Slack interface for the team's hackathon build.
The workflow adapter is ready to connect; the repository does not yet include
the real referee, file executor, or OpenRouter worker agents.

The extension installs into a Slack workspace as a Slack app. Users request work
and approve actions inside Slack; the Python service handles those interactions
in the background. This implements the Slack Bolt + Socket Mode path in the
team's build plan.

The official [Agents, Everywhere starter kit](https://github.com/CopilotKit/agents-everywhere-starter-kit/tree/main/apps/channel)
also offers a Slack template using TypeScript and managed CopilotKit Channels.
That is a separate integration path, with its own Channel onboarding and
credentials. This implementation uses Bolt directly and does not claim to be
connected to CopilotKit. No starter scenario code was copied into this branch.

Your Slack portion is implemented here: one `/referee` command with `report`,
`cleanup`, and `status` subcommands; report and cleanup cards; approval buttons;
message updates; and fresh review cards after report approval.

**Start with [SLACK_SETUP.md](SLACK_SETUP.md).** It walks through app creation,
token setup, installation, and your first `/referee status` reply.

## What works now

- Commands acknowledge receipt before backend work starts.
- Report and cleanup requests post a progress card, then update it with the result.
- Only `REVIEW` cleanup items have approval buttons. `BLOCK` and `DEFER` show reasons.
- Buttons send the actual Slack user, workspace, channel, object ID, and reviewed
  revision to the backend. The backend makes the authorization decision.
- Accepted approvals refresh the clicked card and post an outcome. Publication
  can return new cleanup cards that require fresh approval.
- `/referee status` is private to the requester. It includes current approval
  controls for the latest report and cleanup review, so stale cards have a recovery path.
- Rejections stay private and leave the shared card unchanged.
- Model or user text is rendered as literal text rather than active Slack mentions.

The UI **does not** execute agents, classify file protection, write reports,
quarantine files, or persist task state. Those are Persons 1, 3, and 4's parts.

## Run while teammates build

After following the token and dependency setup in `SLACK_SETUP.md`:

```bash
python run_slack.py --preview
```

This connects the interface to Slack using clearly labelled **in-memory sample
state**. It performs no AI calls or file operations and provides no actual file
protection. The labels remain visible on approval cards and outcomes. Restarting
the process resets preview state; old cards will then be unavailable.

In the same Slack channel:

1. Run `/referee report`. A sample draft awaits its requesting owner's approval.
2. Run `/referee cleanup`. Source data is marked blocked, the working input
   deferred, and the debug log eligible for approval in the sample state.
3. Approve the debug log. The result explicitly says quarantine was simulated.
4. Approve the report. A new cleanup card allows a fresh review of the input,
   provided another sample report in the channel does not still need it.
5. Try another Slack user clicking an approval: the sample backend rejects it.

This is a UI rehearsal. Record the hackathon demo after connecting the real backend.

## Files to hand your integration lead

| File | Purpose |
|---|---|
| `slack_ui.py` | Person 2's handlers and Slack card rendering |
| `slack_manifest.yaml` | Slack app configuration with minimal scopes |
| `slack_contract.py` | UI view models and workflow adapter interface |
| `requirements-slack.txt` | Dependencies to merge into the team's requirements |
| `run_slack.py` | Optional local runner; unnecessary if Person 1 already owns a runner |
| `preview_backend.py` | Sample-only backend for UI development |
| `tests/` | Offline behavior checks, no tokens required |

`slack_contract.py` is a presentation boundary, not a replacement for the team's
`schemas.py`. Person 1 should map their existing models into these view objects.

## Connect to Person 1's app

If the integration lead already has a Bolt `App`, register the handlers once:

```python
from slack_ui import register_handlers
from your_adapter import create_backend

# app is Person 1's existing Bolt App.
register_handlers(app, create_backend())
```

Use the team's existing Socket Mode runner. Do not start a second bot process
for these handlers. For a standalone runner with a completed adapter:

```bash
python run_slack.py --backend your_adapter:create_backend
```

Replace `your_adapter` with an actual Python module. Its factory takes no
arguments and returns an object implementing these five methods:

| Method | Return type | Connection to the team plan |
|---|---|---|
| `start_report(context)` | `ReportView` | Starts or retrieves the requested report workflow |
| `start_cleanup(context)` | `CleanupView` | Runs cleanup proposal and referee evaluation |
| `get_status(context)` | `StatusView` | Returns the requesting user's current views in this channel |
| `approve_report(task_id, revision, context)` | `ApprovalResult` | Checks ownership and draft revision, publishes, then reassesses deferred cleanup |
| `approve_cleanup(action_id, revision, context)` | `ApprovalResult` | Calls the controlled executor after current authorization and eligibility checks |

The backend must declare `preview = False` only when it uses the real workflow.
Every sample or simulated adapter must declare `preview = True`.

`context` contains:

```python
RequestContext(user_id, team_id, channel_id, request_id)
```

The first three values come from the Slack payload delivered through Bolt and
Socket Mode. `request_id` is stable on a redelivery of the same event. Use it to
deduplicate command creation in the backend. Approval idempotency must be keyed
by the action/task and revision because another click creates a new Slack event.

For the original team functions, map `context.user_id` to `owner_slack_id`,
`requester_slack_id`, or `approver_slack_id` as appropriate, and carry
`context.channel_id` and `context.team_id` into stored ownership and scope.
Extend the approval boundary to include the reviewed revision if it currently
accepts only an object ID and user ID.

### Required behavior at the backend boundary

- Store ownership, channel/workspace, revisions, and action status on the server.
  A button's object ID and revision are untrusted references, never authorization.
- A report revision must identify the draft the owner reviewed. Do not publish a
  changed draft or release its input merely because an output file exists.
- Recheck policy, dependencies, authorization, and file version at execution
  time using the same coordination mechanism as task dependency updates.
- Bind approval to the exact proposed operation and file version, keep repeated
  clicks idempotent, and serialize or otherwise coordinate mutations.
- Return current state after accepted approval. `ApprovalResult.report` or
  `.cleanup` refreshes the clicked card. `followups` contains fresh cleanup
  views after report publication, never automatically executed actions.
- Return `accepted=False` for a rejected approval, or raise
  `WorkflowError("message safe for the requester")`. Do not include credentials
  or private exception details in that message.
- Populate `audit_id` only if the real backend created an audit record.
- Keep methods synchronous for this Bolt runner. Set model/network timeouts in
  the backend so calls finish rather than occupying workers indefinitely.
- Return no more than 12 candidates in one `CleanupView`. IDs and revisions
  must be nonempty strings of at most 200 characters. Report drafts shown in
  Slack are excerpts; retain the full report in the workflow's own storage.

The sample backend is intentionally inspectable as an adapter example. Its
sample decisions are not a production referee implementation.

## Verification

Run without Slack credentials or installed SDK dependencies:

```bash
python -m unittest discover -s tests -v
```

The checks exercise acknowledgement order, identity propagation, malformed
payload rejection, stale approvals, duplicate actions, private errors, fresh
review cards, literal rendering, and the complete sample state transition.

These tests use mocked Slack API calls. The GitHub Actions workflow installs the
real SDK and also exercises Bolt dispatch without credentials. Locally, the SDK
wiring tests skip when that dependency is absent. No test opens a live Slack
connection. The first live gate is `/referee status` after installing dependencies
and adding your app tokens locally.

If a Slack message update fails after a backend action completed, the UI does
not execute the action again. It asks the user to check `/referee status`.
Correct backend idempotency is still required for a later retry.

## Current limits

One installed workspace, one local Socket Mode process, and channel-based
commands/buttons. No message-history access, cross-workspace Slack Connect
workflow, App Home, database, model provider, or arbitrary filesystem control.
Keep the preview backend out of claims about real execution.

## Official references

- [Socket Mode with Bolt for Python](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/)
- [Acknowledging commands and actions](https://docs.slack.dev/tools/bolt-python/concepts/acknowledge/)
- [Handling button actions](https://docs.slack.dev/tools/bolt-python/concepts/actions/)
- [Block action payload fields](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/)
- [Slack app manifest reference](https://docs.slack.dev/reference/app-manifest/)
