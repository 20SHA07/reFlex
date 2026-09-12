# reFlex AI agents: Windows setup and team handoff

This package connects your OpenRouter agents to the uploaded browser extension and local referee workflow. The local application entry point is **`browser_bridge.py`**. You can run this package now without additional `app.py`, `store.py`, `schemas.py`, or `main.py` files.

This version incorporates `reFlex-main (3).zip`: the report and cleanup agents
both use `logs/agent_activity.log`. A pending report reserves that log, cleanup
is deferred, and approved report publication releases it for a new cleanup
review. Cleanup uses recoverable quarantine after a separate approval.

There are **two worker agents**: Report and Cleanup. A separate, constrained explanation helper chooses a recovery plan from verified referee decisions. It cannot change those decisions. All three functions share one OpenRouter client, model setting, and API key.

## 1. Open the project on Windows

Extract the ZIP. Open the folder containing `agents.py`, `demo_agents.py`, and `browser_bridge.py`. In File Explorer's address bar, type `powershell` and press Enter.

```powershell
py --version
```

Use Python 3.10 or newer. These Python files use the standard library; no Python package installation is required for the agent and local bridge demonstrations. If `py` is unavailable but Python is installed, replace `py` with `python` in the commands below.

## 2. Check the code offline first

```powershell
py demo_agents.py
py -m unittest discover -s tests -v
Push-Location referee_agent
py -m unittest discover -s tests -v
Pop-Location
```

The first command prints a scripted report, cleanup proposal, and explanation from sample data. It makes no AI requests, publishes no report, and moves no files. The explanation rows in this standalone demonstration are supplied examples; they are not real referee evaluations of its cleanup proposal. The tests use mocks or temporary fixtures and do not require your API key.

For the extension's JavaScript tests, with Node.js 22 or newer installed:

```powershell
Push-Location browser-extension
npm test
Pop-Location
```

The `npm test` script runs Node's built-in test runner. No JavaScript build or package installation is needed to load the extension or run this test script.

Package validation: 61 agent/bridge tests, 30 referee/executor tests, and 24
JavaScript tests passed in the development environment. Both offline agent demo
modes passed. This includes a scripted agent tool loop connected to the real
referee and approval workflow on temporary files. It does not establish live
OpenRouter availability or replace a browser check on your Windows computer.

## 3. Set one OpenRouter API key

Create an inference API key in your team's OpenRouter account on the [API Keys page](https://openrouter.ai/settings/keys). One key serves both agents and the explanation helper. See [OpenRouter authentication](https://openrouter.ai/docs/api_reference/authentication).

Run this command, paste only your key at the hidden prompt, then press Enter:

```powershell
$reflexKey = Read-Host "Paste your OpenRouter API key" -AsSecureString
```

Then run:

```powershell
$env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new("", $reflexKey).Password
$env:OPENROUTER_MODEL = "openai/gpt-4.1-mini"
```

Start the Python commands from this same PowerShell window. These settings last for that terminal session. The code reads environment variables; creating a `.env` file alone does not load them.

Keep the key on the backend. Do not put it in the extension, commit it, or send it to a teammate in a screenshot. Model requests consume account credits; model access and limits depend on your account. The configured model's details are on the [GPT-4.1 Mini page](https://openrouter.ai/openai/gpt-4.1-mini).

## 4. Resolve the current credits error and test the model

Your latest diagnostic accepted the key (HTTP 200), then returned **HTTP 402:
insufficient credits; the account has never purchased credits**. The current
blocker is funding the account for the paid `openai/gpt-4.1-mini` model. Add
credits to the account or organization that owns your key on the
[OpenRouter credits page](https://openrouter.ai/settings/credits), then run the
diagnostic below. Both agents can keep sharing that key. No successful live
agent run has been confirmed yet.

Run the included staged diagnostic:

```powershell
py diagnose_openrouter.py
```

It checks the public API, checks the key without generating text, sends a minimal request to the same model with no tools and up to 32 output tokens, then runs the original agent demonstration if those checks pass. It stops at the failing stage. The minimal request and subsequent agent calls can incur charges. The diagnostic keeps the existing network and TLS settings and redacts credentials from its selected error output.

Share the lines beginning `[diagnostic]` if it fails; do not share your key. An HTTP status alone is not enough to identify the cause. See [OpenRouter error handling](https://openrouter.ai/docs/api_reference/errors-and-debugging).

After the diagnostic passes, you can run the standalone live demonstration directly:

```powershell
py demo_agents.py --live
```

For a free alternative using the same key, try
[Qwen3 Coder Free](https://openrouter.ai/qwen/qwen3-coder:free):

```powershell
$env:OPENROUTER_MODEL = "qwen/qwen3-coder:free"
py demo_agents.py --live
```

That model is listed as free and supports tool use, but has request limits and
still needs to be verified with this complete agent workflow. Changing this
environment variable also changes the model used by `browser_bridge.py --live`
when launched from the same terminal.

Check the report against the shared log: Monday 12, Tuesday 18, Wednesday 15.
The normal Cleanup Agent can propose the log for review or return no candidates.
This standalone command exercises real model calls but does not publish reports,
move files, or contact Slack.

## 5. Connect the browser extension

1. In Chrome, open `chrome://extensions` and enable **Developer mode**.
2. Choose **Load unpacked** and select this package's `browser-extension` folder, which contains `manifest.json`.
3. Copy the extension's ID from its card. Replace `YOUR_EXTENSION_ID` below with that value.
4. In the PowerShell window containing your OpenRouter settings, start:

```powershell
py browser_bridge.py --live --extension-id YOUR_EXTENSION_ID
```

The bridge listens at `http://127.0.0.1:8765`. Its terminal output prints the path to a private `pairing-token.txt` file and the location of newly created temporary sample files. Open the token file locally and copy its contents into the extension's **Connect local demo** settings. **The pairing token is different from your OpenRouter API key.** The provider key stays in PowerShell and the Python process.

Open a Slack channel in Chrome at `https://app.slack.com/client/...`, open the extension's side panel, allow its requested localhost access, and connect using the pairing token. Use Slack's website for this; the Chrome extension does not load in the Slack desktop application.

The panel should identify the connection as an **OpenRouter demo**. **Use selection** imports only selected text. Starting a live report sends the request text and sample report input to OpenRouter; cleanup sends the authorized sample file inventory, and the explanation helper sends verified decision facts. Choose only text you intend to send.

Start a report, wait for its draft, then review cleanup. The bridge runs model work in a background job while the extension polls for completion. A long request can take several minutes. If the panel times out, refresh its state before starting the operation again.

Read the exact draft before approving publication. Approving publishes that
draft into the disposable workspace. In this version, the shared log is the
only cleanup candidate: it receives DEFER while the report is pending, then a
fresh REVIEW after approved publication. A separate cleanup approval moves it
to quarantine. The normal model may also return no candidates. General
protected-file rules remain in the referee and its tests, but a protected
source file is not part of this one-log demonstration.

For a predictable referee demonstration, stop the bridge with Ctrl+C and start a fresh instance:

```powershell
py browser_bridge.py --live --fault-injection --extension-id YOUR_EXTENSION_ID
```

Pair again with its new token. Start the report before reviewing cleanup. This
option submits the supplied demo inventory as fixed cleanup candidates, so the
shared-log DEFER-to-REVIEW conflict is predictable. The interface labels it as
fault injection. The model does not select the candidate paths; report
generation and the explanation helper still use OpenRouter.

For the same local file workflow with fixed samples and no model calls, use:

```powershell
py browser_bridge.py --extension-id YOUR_EXTENSION_ID
```

Every bridge launch creates new temporary fixtures. The terminal prints their directory so you can inspect the published report, quarantine, and audit evidence. Stopping the bridge invalidates the old pairing token. Reconnect with the new token after restarting.

## 6. Files and interface for your teammate

| Files | Responsibility |
| --- | --- |
| `agents.py` | OpenRouter client, two worker agents, proposal validation, constrained explanation helper |
| `prompts/report.md`, `prompts/cleanup.md`, `prompts/explanation.md` | Role-specific model instructions |
| `demo_agents.py`, `diagnose_openrouter.py` | Independent agent test and staged live diagnosis |
| `tests/test_agents.py`, `tests/test_agent_api.py` | Offline agent and public-interface checks |
| `browser_bridge.py` | Local workflow adapter: task dependencies, model calls, referee decisions, approvals, jobs, and execution |
| `browser-extension/bridge.mjs`, `browser-extension/panel.js` and related interface files | Job polling, displaying live results, and collecting explicit approval |
| `referee_agent/` | The team's deterministic referee and controlled file executor |

The application creates the service once and reuses it:

```python
from agents import OpenRouterClient, ReFlexAgents, VerifiedDecision

ai_agents = ReFlexAgents(OpenRouterClient.from_env())
```

| Method | Host supplies | Result |
| --- | --- | --- |
| `run_report(task_id, *, input_path, input_text, output_path="reports/client_update.md", request_text="")` | Registered task ID, authorized input snapshot, assigned output path, optional request | `ReportDraft` with `task_id`, `input_path`, `output_path`, `markdown`; `.as_action()` returns a proposal dictionary |
| `run_cleanup(task_id, inventory, *, fault_injection=False)` | Host-assigned cleanup request ID and up to 200 authorized relative paths | `CleanupProposal` containing zero to 12 candidates, reason, and source; `.as_actions()` returns proposal dictionaries |
| `explain_cleanup(decisions)` | Up to 12 `VerifiedDecision` objects from the referee | `Explanation` with `text`, `plan`, `model_used`, and optional `error` |

`VerifiedDecision(path, decision, reason, dependency_task_id=None)` carries an authoritative BLOCK, DEFER, REVIEW, or ALLOW result. Empty explanation input returns immediately without a model call. Model failure uses the verified facts and a fixed fallback plan; it does not grant permission.

The host must register report input dependencies before reading input, authorize the task and files, and retain ownership and revision state. Report input text is limited to 20,000 characters and request text to 2,000. Paths use relative forward-slash notation, such as `logs/agent_activity.log`.

Agent results are proposals. Your teammate's application remains responsible for saving task/action state, checking current file versions, applying the referee, verifying approvals, and executing through the controlled executor. These functions are synchronous, so the host should acknowledge the UI request before running them. The included bridge already demonstrates that connection through background jobs.

## Current limits

- The bridge is a local browser demonstration with disposable files and one paired owner. Slack page context is not verified Slack identity; it does not post Slack messages or act as a production Slack bot.
- Workflow and approval state live in memory. Files and audit evidence may remain in the temporary directory, but they do not restore an interrupted workflow.
- If report generation fails after its input dependency is reserved, that fixture pauses. Restart the bridge and pair again for a fresh demonstration; retrying in the paused fixture will not complete it.
- Offline checks validate the code with scripted or mocked responses. Successful live OpenRouter calls, account access, and the complete browser workflow still require testing on your computer. Nobody needs your API key to review the code or help diagnose redacted errors.
