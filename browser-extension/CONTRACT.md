# Browser extension bridge contract
This is Person 2's browser interface, not the Slack bot from PR #1.

The extension is Chromium Manifest V3, with a native side panel. Content scripts
only return Slack workspace/channel URL context and, on explicit request,
selected text. Page context is untrusted routing context, never authentication.

The service worker alone contacts http://127.0.0.1:8765. A paired token is kept
in chrome.storage.session (trusted extension contexts only). The local service
authenticates that token to a configured local demo principal, not a Slack identity.

GET /v1/session (Authorization: Bearer token) returns:
{"ok":true,"principal":{"id":"local-owner","display_name":"Local demo owner"},"mode":"local-demo","generation":"sample"}
generation is "sample" by default or "openrouter" with --live. The local-demo
mode describes disposable files and local owner identity in both cases.

POST /v1/dispatch with the same Authorization header:
{"operation":"start_report","request_id":"uuid","context":{"workspace_id":"T123","channel_id":"C123","url":"https://app.slack.com/client/T123/C123"},"input":{"text":"optional request"}}
Operations: status, start_report, start_cleanup, approve_report, approve_cleanup.
Approvals additionally carry "target":{"id":"stored object id","revision":"stored revision"}.
The server assigns owner identity and rechecks all authorization, version and dependency rules.
Use a single serialized dispatch boundary. Idempotency is request_id + context for starts,
and stored object ID + revision for approvals. Do not store secrets in events or responses.

Success response is {"ok":true,"state":STATE}. Error response is
{"ok":false,"error":{"code":"stale_review","message":"Request a fresh review."}} with suitable status.

In OpenRouter mode, POST /v1/dispatch first returns HTTP 202:
{"ok":true,"job_id":"32-lowercase-hex-id"}.
Poll GET /v1/jobs/JOB_ID with the same token. HTTP 202 means pending; completion
returns the original success/error response and status. Polling never executes
or repeats an action. The service worker submits once, bounds each HTTP request
to 15 seconds, and polls for at most 240 seconds including its queue time.
On timeout, refresh status before starting the operation again. Existing request
IDs and exact review revisions still control deduplication and approvals.

STATE:
{
 "mode":"preview",
 "principal":{"id":"preview-owner","display_name":"Preview owner"},
 "context":{"workspace_id":"T123","channel_id":"C123"},
 "report":null,
 "cleanup":null,
 "events":[],
 "message":"Ready."
}
A report is:
{"id":"uuid","revision":"uuid","status":"awaiting_approval","input_path":"logs/agent_activity.log","output_path":"reports/activity-report-uuid.md","draft":"reviewable report text"}
Published reports use status "completed".
A cleanup is:
{"id":"uuid","items":[{"id":"uuid","revision":"uuid","path":"logs/agent_activity.log","verdict":"DEFER","reason":"Report Agent is using the shared log.","executed":false}]}
Other verdicts: DEFER, REVIEW, ALLOW. Only REVIEW renders an approval button.
Connected state also includes generation. Cleanup may include explanation
(plain text), explanation_model_used and explanation_error (booleans), and
proposal_source (sample, model, or fault_injection). Explanations never replace
item verdicts, reasons, revisions, or execution state. After state changes, the
bridge refreshes rule explanations so old AI wording cannot describe stale facts.
Events: {"id":"uuid","kind":"info","text":"human-readable outcome","at":"ISO timestamp"}; newest last, maximum 20.

Browser service worker message contract, accepted only from panel.html:
{type:"GET_CONTEXT",selection:false} -> {ok:true,context:{workspace_id,channel_id,url,selected_text?}}
{type:"GET_CONFIG"} -> {ok:true,mode:"preview"|"local-demo",generation?:"sample"|"openrouter",principal?:...}
{type:"CONNECT",token:"paired token"} -> same config shape (UI first requests optional localhost permission)
{type:"DISCONNECT"} -> preview config
{type:"DISPATCH",operation:"status",context:CONTEXT,input?:{text},target?:{id,revision},request_id:"uuid"} -> server-shaped response.
DISPATCH re-reads active Slack context and rejects a mismatched workspace/channel before action.
The panel must refresh context on visibility/tab changes and disable actions without Slack context.
Request current status to refresh after stale errors. The UI never auto-approves new proposals.
Default browser preview stores only sample state in session storage and performs no file operations.
The local demo bridge uses the real inherited executor on a newly created disposable fixture.
Default mode generates a deterministic sample report. --live uses backend
OpenRouter agents on the same fixtures; display generation truthfully while
retaining local-demo mode. Provider credentials never enter the extension.
