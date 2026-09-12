# OpenRouter integration contract

Optional Python backend integration. The extension never receives provider keys.
Keep `mode: local-demo` for the disposable file host. Add an `agent` descriptor
to `/v1/session`, state, and extension config:
`{"provider":"openrouter","model":"google/gemini-2.5-flash"}` or
`{"provider":"sample","model":null}`. Do not claim AI in sample or preview mode.

`openrouter_agents.py` exposes `OpenRouterAgents(api_key: str, model: str = DEFAULT_MODEL)`
and `OpenRouterError(code, message)` with sanitized public errors. DEFAULT_MODEL is
`google/gemini-2.5-flash`. The object has `.model` and `.descriptor` properties.

Methods (keyword arguments):
- `generate_report(input_path: str, source_text: str, request_text: str) -> str`
- `propose_cleanup(candidates: list[dict], request_text: str) -> list[dict]`

Each candidate is host supplied: `{path, size_bytes, sha256}` (size/hash may be
null for absent/unreadable files). Do not send file bodies for cleanup. Its
default task is to prepare quarantine requests for all these sample candidates
for the referee to review, with one reason per candidate. This is a bounded
conflict demonstration, not discovery of arbitrary real files. The model must
return exactly one `{path, operation: 'quarantine', reason}` for each candidate,
no duplicates, arbitrary paths, executor verdicts, or approval claims. Validate
the whole plan before passing any proposal to the referee. Reasons are model
suggestions, distinct from authoritative referee explanations in UI.

Report returns the model's exact Markdown draft. It is a proposal; no model can
approve/publish or complete tasks. Keep existing current hash, revision, owner,
and dependency checks. Capture candidate file hashes BEFORE calling the model
and pass expected_hash to evaluation after response. Approvals never call a model.

The provider client makes one bounded non-streaming POST per agent start to
https://openrouter.ai/api/v1/chat/completions using strict JSON schema outputs,
provider.require_parameters=true, max_tokens=1600, and no hidden retries or
fallback to sample. Reject malformed/refused/truncated/oversized responses,
unauthorized fields, invalid paths, and control characters. Network failures
must not expose API response bodies or credentials. Separate system instructions
from explicitly untrusted request text and file contents.

CLI: `python browser_bridge.py --openrouter --extension-id EDGE_ID`
Read OPENROUTER_API_KEY from environment; otherwise prompt with getpass, only
when stdin is a TTY. Allow --model or OPENROUTER_MODEL override. No key in CLI
arguments, source, prompts sent to model, responses, or extension storage.
No --openrouter keeps current offline sample workflow.

Report generation failure keeps acquired dependency reserved and leaves fixture
paused with the public provider error plus explicit restart instructions.
Cleanup generation failure creates no proposals and cannot replace an existing
review. The user sees the error; no silent fallback.

Browser worker should preserve only validated agent provider/model metadata in
connection config and increase bridge fetch timeout to 28 seconds for model
operations. Sidebar labels OpenRouter/model when configured and shows that
request text and demo report data are sent to OpenRouter on Start. Requests still
come from an explicitly selected Slack context. Modes remain preview/local-demo.

Testing uses a fake provider transport and injected provider object, not live
OpenRouter credentials. Browser smoke still runs against offline sample mode;
unit/integration checks must cover provider flow through the real referee and
UI truthful labels. Production credentials must never be needed by CI.

The user also explicitly needs TWO test agents. Implement separate `ReportAgent`
and `CleanupAgent` classes in `test_agents/report_agent.py` and
`test_agents/cleanup_agent.py`, exported by `test_agents/__init__.py`. Both accept
an optional OpenRouterAgents provider, and expose `.agent_id` (report-agent or
cleanup-agent), `.descriptor`, and `.run(...)` with the same keyword arguments
as the relevant provider method. Their offline implementations reproduce sample
behavior. BrowserBridge/DemoSession must instantiate and call these two actual
agents, not leave them as unused example classes. Neither agent gets an executor
or approval function; the host owns policy, execution, dependency registration,
and human approvals. A root-owned run_test_agents.py will exercise this pair via
BrowserBridge and show conflicts; it never auto-approves file operations.
