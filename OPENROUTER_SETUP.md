# Run two test agents with OpenRouter

The Report Agent drafts a client update from a demo working file. The Cleanup
Agent prepares quarantine proposals for the three demo candidates. OpenRouter
generates the draft and proposal reasons. The referee owns the decisions and
file operations; a model cannot grant approval or release a dependency.

Both agents have offline sample implementations for rehearsing without credits.
Use `--openrouter` to make real model calls. There is no automatic fallback to
sample output when a provider request fails.

## Update your existing checkout

The extension and integration are on `feat/browser-extension` while PR #2 is open.
From your repository folder:

```sh
git switch feat/browser-extension
git pull --ff-only origin feat/browser-extension
```

You need Python 3.10 or newer. No Python packages need to be installed. Use
`python3` on macOS/Linux or `py` on Windows if `python` is unavailable.

## Obtain your own API key

Create an API key in your [OpenRouter key settings](https://openrouter.ai/settings/keys)
after adding credits to your own account. An event redemption code is not an API
key. The setup does not redeem sponsor offers or include event codes.

The commands below prompt for the key with input hidden. Paste it into your
terminal and press Enter. The key is held by the Python process; it is not saved
by Truce or sent to the browser extension. Do not paste the key into Slack,
extension settings, a terminal command argument, or your repository.

For automated local runs you can instead provide `OPENROUTER_API_KEY` through
your environment or secret manager. The program does not load `.env` files.

## Test the pair from the terminal first

```sh
python run_test_agents.py --openrouter --show-report
```

This makes one report request and one cleanup request to OpenRouter. It prints
the report draft and the referee decisions, without approving any file changes:

| Candidate | Expected decision |
| --- | --- |
| `data/source_metrics.csv` | BLOCK: protected data |
| `working/report_input.csv` | DEFER: needed by the report task |
| `scratch/debug.log` | REVIEW: human approval required |

This is an explicit conflict test: the cleanup agent is asked to prepare a
quarantine request for each of three supplied candidates. It does not discover
arbitrary files on your computer. Its suggested reasons are displayed separately
from the referee's authoritative policy decision.

The terminal command never auto-approves a proposal. It prints the disposable
fixture directory so you can inspect the files and audit trail. The report stays
a draft in the review state until approved through the browser workflow.

To run the same pair without a provider call:

```sh
python run_test_agents.py --show-report
```

## Run the full flow in Edge and Slack

1. Open `edge://extensions`, enable **Developer mode**, and load the
   `browser-extension` directory with **Load unpacked**. If it is already loaded,
   click **Reload** on its card after updating your checkout.
2. Copy the extension ID shown in Edge. From the repository root, run:

   ```sh
   python browser_bridge.py --openrouter --extension-id YOUR_EDGE_EXTENSION_ID
   ```

3. Enter your OpenRouter API key at the hidden terminal prompt. Keep this process
   running. The terminal prints a separate private `pairing-token.txt` path.
4. Open Slack in Edge on the same computer and reload the channel. Open Truce's
   sidebar, expand **Connect local demo**, and paste the **pairing token** from
   that file. Allow localhost access. The pairing token and provider API key are
   different; only the pairing token goes into the extension.
5. The sidebar should identify **OpenRouter** and the configured model. A local
   connection alone does not mean model calls are enabled.
6. Optionally highlight a Slack message and press **Use selection**. Review the
   text, then press **Start report**. The model receives that text and the demo
   CSV input and returns the reviewable draft.
7. Press **Review cleanup**. A separate model request prepares cleanup proposals.
   The referee returns BLOCK, DEFER, and REVIEW for the sample scenario.
8. Approve the debug log's quarantine, read and approve the report, then approve
   the fresh cleanup proposal for the released working input.

Report/cleanup starts call OpenRouter. Pairing, status refreshes, and approvals
do not call the model. Repeated new start requests can incur additional API usage;
the client does not automatically retry a failed call.

For Chrome, use `chrome://extensions` for the same setup. The automated browser
test uses Chromium; native Edge sidebar opening and permission prompts should be
checked on your machine.

## Model choice and errors

The default is `google/gemini-2.5-flash`. To select another model that supports
strict structured outputs, pass its OpenRouter model ID:

```sh
python run_test_agents.py --openrouter --model YOUR_PROVIDER/MODEL_ID
```

`OPENROUTER_MODEL` is also supported. The backend requires the provider to support
the requested structured output parameters and validates the returned content
itself. See [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
and the [model listing](https://openrouter.ai/models).

If authentication, credits, rate limits, timeouts, or malformed model output
prevent a request, the program reports an error and keeps approval rules intact.
It never silently substitutes an offline report. A failed report after reserving
its input pauses that fixture; restart the bridge and pair with the new token.
A failed cleanup generation leaves the previous review untouched.

## Code and verification

- `test_agents/report_agent.py`: Report Agent, model-backed or offline.
- `test_agents/cleanup_agent.py`: Cleanup Agent, model-backed or offline.
- `openrouter_agents.py`: bounded HTTPS provider calls and output validation.
- `browser_bridge.py`: host-owned task dependencies, reviews, approvals, executor.
- `run_test_agents.py`: direct two-agent conflict test with no automatic approval.

From the repository root:

```sh
python -m unittest discover -s tests -v
node --test browser-extension/tests/*.test.mjs
```

Automated provider tests use a fake transport so CI needs no API key or credits.
They verify actual referee behavior for model proposals, including invalid
output, changed file hashes, protected targets, and fresh approvals. A live
OpenRouter call must be checked locally with your account.
