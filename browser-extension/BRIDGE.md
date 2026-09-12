# Connect the extension to real local file operations

The browser extension opens beside Slack's website. The bridge below connects
that interface to `referee_agent/executor.py`, using disposable sample files.
It does not require a Slack app or Slack bot token. The default offline mode
needs no model API key. To use the two test agents with real OpenRouter calls,
follow [OPENROUTER_SETUP.md](../OPENROUTER_SETUP.md).

## Start the local demo

1. Load `browser-extension/` as an unpacked extension in your browser's extension
   developer mode. Copy its 32-letter extension ID from `edge://extensions` in
   Edge or `chrome://extensions` in Chrome.
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
5. Start a sample report, then review cleanup. Approve the debug log's quarantine,
   inspect and approve the report, then approve the fresh working-input cleanup.

If pairing fails, confirm the extension ID and that this terminal process is
still running. Reloading an unpacked extension can clear its session connection;
pair again. A bridge restart creates a new token and fresh fixtures, so old
approval cards cannot authorize the new process.

## What the demo proves

| File | Initial cleanup decision after starting the report | Visible result |
| --- | --- | --- |
| `data/source_metrics.csv` | BLOCK | Protected source stays unchanged; approval cannot override it. |
| `working/report_input.csv` | DEFER | The report reserves this input until publication is approved. |
| `scratch/debug.log` | REVIEW | Explicit approval moves the file into quarantine. |

Report approval publishes the exact draft to a unique path under `reports/`,
then releases the input dependency. The executor re-evaluates deferred cleanup
with a new action ID. It requires a new approval; the old decision cannot perform
the new operation. File hash changes prevent an outdated cleanup review from
executing. Quarantine and an audit journal remain outside the sample workspace.

The terminal prints the disposable root directory for inspecting the generated
report, source data, quarantine, and `private_state/audit.jsonl`. Each Slack
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
- Without `--openrouter`, the two agents produce **deterministic samples**. With
  `--openrouter`, model calls generate the report and cleanup proposal reasons.
  The sidebar identifies the configured provider/model. Selected request text
  and demo report data are sent to OpenRouter only when you start an agent.
- The existing executor protects operations sent through it. This is not an OS
  sandbox and cannot stop unrelated programs from editing files directly.
- State is single-process and in memory. Restart the bridge for a fresh scenario.
  The append-only audit journal is evidence, not resumable approval state.

`test_agents/report_agent.py` and `test_agents/cleanup_agent.py` implement the two
proposal-only workers. Their provider credentials stay on the backend. Task
registration, immutable proposals, local owner approval, current hash checks, and
controlled execution remain with the trusted host. Never put provider keys in
extension code or treat text from Slack's DOM as approval identity.

Run the bridge checks from the repository root:

```sh
python3 -m unittest discover -s tests -v
```
