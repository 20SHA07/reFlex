# Connect the extension to real local file operations

The browser extension opens beside Slack's website. The bridge below connects
that interface to `referee_agent/executor.py`, using disposable sample files.
The default sample mode does not require a Slack app, Slack bot token, or model
API key. For live Report and Cleanup agents, use `browser_bridge.py --live`
with the same extension ID. Follow the [Windows OpenRouter setup](../AI_AGENTS.md)
first; the provider key stays in Python, and the extension uses a separate
local pairing token.

## Start the local demo

1. Load `browser-extension/` as an unpacked extension in Chrome's extension
   developer mode. Copy its 32-letter extension ID from `chrome://extensions`.
2. From the repository root, run Python 3.10 or newer:

   ```sh
   python3 browser_bridge.py --extension-id YOUR_EXTENSION_ID
   ```

3. The terminal shows a private `pairing-token.txt` path. Open that file locally
   and copy its contents into the extension's local connection settings. Do not
   send the token to Slack or commit it. The bridge only listens on
   `http://127.0.0.1:8765`.
4. Open a channel on `https://app.slack.com/client/...`, open the extension's side
   panel, allow its requested localhost access, and connect with that token.
5. Start a sample report, then review cleanup. The referee defers the shared-log
   deletion request until you approve the report; then approve the fresh log cleanup.

If pairing fails, confirm the extension ID and that this terminal process is
still running. Reloading an unpacked extension can clear its session connection;
pair again. A bridge restart creates a new token and fresh fixtures, so old
approval cards cannot authorize the new process.

## What the demo proves

| File | Initial cleanup decision after starting the report | Visible result |
| --- | --- | --- |
| `logs/agent_activity.log` | DEFER | The report agent reserves this shared log until publication is approved. |

Report approval publishes the exact draft to a unique path under `reports/`,
then releases the shared-log dependency. The executor re-evaluates deferred cleanup
with a new action ID. It requires a new approval; the old decision cannot perform
the new operation. File hash changes prevent an outdated cleanup review from
executing. Quarantine and an audit journal remain outside the sample workspace.

The terminal prints the disposable root directory for inspecting the generated
report, shared activity log, quarantine, and `private_state/audit.jsonl`. Each Slack
workspace/channel gets its own subdirectory and workflow. Stopping the bridge
invalidates its token and removes the token file; fixture evidence remains in
the operating system's temporary directory for inspection. Remove that specific
temporary demo directory manually when finished if desired.

## Boundaries and integration

- The token authenticates **one local demo owner**. Slack workspace/channel IDs
  are untrusted page routing context, not verified Slack user identity.
- The bridge accepts only the documented operations on its fixed, newly created
  fixtures. It never takes a production workspace path or a shell command.
- Its HTTP boundary checks the loopback Host, bearer token, request size, and
  configured extension Origin. Browser-page requests from Slack itself are denied.
- Default reports are **deterministic samples**. With `--live`, OpenRouter drafts
  from the sample input and selected request text. The panel labels that mode,
  and approval is still required before publication. Selected text never grants
  file permissions or becomes a shell command.
- The existing executor protects operations sent through it. This is not an OS
  sandbox and cannot stop unrelated programs from editing files directly.
- State is single-process and in memory. Restart the bridge for a fresh scenario.
  The append-only audit journal is evidence, not resumable approval state.

The optional backend agent integration is implemented in `agents.py` and
`prompts/`. Live requests run as jobs so the panel can poll without holding a
long HTTP request open. Task registration, immutable proposals, local owner
approval, hash checks, and execution remain on the backend. Never put provider
keys in extension code or treat Slack DOM text as approval identity.

Run the bridge checks from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_browser_bridge.py' -v
```
