# reFlex: Agent Referee

An agent referee for Slack's website. reFlex coordinates work on shared files,
keeps active inputs available, and requires human approval before eligible file
operations run. An Edge/Chrome side panel shows the review; an Ember sprite on
the Slack page reacts to the referee's decisions.

The current `main` demo has two workers sharing `logs/agent_activity.log`. The
report worker reserves the log while preparing a draft. The cleanup worker asks
to remove that same log. The referee defers cleanup until the report is approved,
then creates a fresh cleanup review for the owner to approve separately.

## How it works

The browser extension supplies Slack workspace/channel context and shows review
controls. A paired localhost bridge coordinates tasks and sends proposals to the
Python referee. The referee checks protected paths, active dependencies, file
state, and approvals. The controlled executor publishes the approved report or
moves an approved cleanup target to quarantine, recording the outcome in an
audit journal.

Before execution, the host checks the current state again. A changed file, a new
dependency, or an already-used action can invalidate an earlier review. The
browser never receives unrestricted filesystem authority.

## Which version should I use?

| Version | What it contains | Start here |
| --- | --- | --- |
| `main` | Integrated side panel, sprite, Python referee, and shared-log demo with deterministic sample workers | The quick start below |
| `feat/browser-extension` | OpenRouter-backed ReportAgent and CleanupAgent, using the earlier three-file scenario | [OpenRouter setup](https://github.com/20SHA07/reFlex/blob/feat/browser-extension/OPENROUTER_SETUP.md) |
| `sprite/` on `main` | Standalone visual development extension | [Sprite development](sprite/README.md) |

OpenRouter is available on the feature branch. The current `main` bridge does
not accept `--openrouter`, and its sample reports do not call an AI model. The
feature branch currently has a different demo and does not include main's
integrated sprite. Use each branch's matching setup instructions.

## Quick start in Edge or Chrome

You need Python 3.10 or newer and Slack open in a desktop browser. Node.js 22+
is only needed for JavaScript tests. The main demo needs no Python package
installation, JavaScript build, Slack bot token, or model API key.

Get `main`:

```sh
git clone --branch main https://github.com/20SHA07/reFlex.git
cd reFlex
```

If you already have the repository, stop any running bridge with `Ctrl+C`, then
update your checkout:

```sh
git fetch origin
git switch main
git pull --ff-only origin main
```

1. Open `edge://extensions` in Edge or `chrome://extensions` in Chrome. Enable
   **Developer mode**, select **Load unpacked**, and choose `browser-extension/`,
   the folder containing `manifest.json`.
2. Find **reFlex · Agent Referee** on that page and copy its extension ID. If you
   updated an existing installation, click **Reload** on its card.
3. From the repository root, start the local bridge:

   ```sh
   python browser_bridge.py --extension-id YOUR_EXTENSION_ID
   ```

   Replace `YOUR_EXTENSION_ID` with the ID from your browser. Use `python3` or
   `py` if that is your Python command. Keep this terminal running.
4. The terminal prints a private `pairing-token.txt` path and the location of
   the disposable demo files. Open the token file locally and copy its contents.
5. Open Slack on the same computer at `https://app.slack.com/client/...`, enter
   a channel, and reload the page. Open reFlex from the browser's Extensions
   menu or pinned toolbar icon. In Edge, **Open in sidebar** is also available
   from the extension's menu.
6. Expand **Connect local demo**, paste the pairing token, connect, and allow
   localhost access. The badge should change from **Preview** to **Local demo**.

Load only `browser-extension/` for this flow. It already includes the sprite;
loading `sprite/` separately would add a second overlay. Slack's desktop app
does not load this browser extension.

## Run the shared-log demo

Optionally select text in the Slack channel and press **Use selection** to add
it to the request. The current main report uses a deterministic sample and
quotes the supplied request context.

| Step | Action | Expected result |
| --- | --- | --- |
| 1 | **Start report** | A report draft appears. Its dependency on `logs/agent_activity.log` stays active. |
| 2 | **Review cleanup** | The log receives **DEFER**. It cannot be approved while the report needs it. |
| 3 | Read the draft, then **Approve & publish report** | The reviewed draft is written under `reports/`, and the log's dependency is released. |
| 4 | Inspect the refreshed cleanup card | A new **REVIEW** proposal appears with a new action ID. No cleanup has run yet. |
| 5 | **Approve quarantine** | The log moves to quarantine and the card shows the completed outcome. |

The host implements cleanup as quarantine. It does not permanently delete the
file. A released dependency alone never approves an old cleanup proposal.

To verify the disk changes, open the disposable root printed in the terminal.
Each Slack workspace/channel has its own generated session folder. Inside it,
inspect `workspace/reports/`, `workspace/logs/`, and
`private_state/quarantine/`. The audit journal is `private_state/audit.jsonl`.

Switch Slack channels to check that reviews stay with their channel. To start
fresh, stop the bridge with `Ctrl+C`, run it again, and pair with its new token.
Its temporary fixture and audit evidence remain available for inspection.

## Preview and sprite behavior

**Preview** runs in browser session storage and changes no files. **Local demo**
uses the Python executor on newly created disposable files. The sprite receives
current review state from the extension's service worker in both modes; it has
no file or approval authority.

| Referee state | Sprite card | Meaning |
| --- | --- | --- |
| `BLOCK` | Red | Policy prevents the operation. |
| `DEFER` | Yellow | An active task still needs the file. |
| `REVIEW` | Yellow | The proposal needs human approval. |
| `ALLOW` | Green | The host reports the allowed outcome. Check the panel for execution status. |

The panel distinguishes **DEFER** from **REVIEW**, even though both use a yellow
sprite card. For visual rehearsal, `Alt+Shift+E` shows green, `Alt+Shift+W` shows
yellow, and `Alt+Shift+R` shows red. These shortcuts do not call the backend or
approve a file operation.

## Test the code

From the repository root, run the bridge and extension checks:

```sh
python -m unittest discover -s tests -p 'test_browser_bridge.py' -v
node --test browser-extension/tests/*.test.mjs
```

Run the core referee's tests and its separate three-file demonstration:

```sh
cd referee_agent
python -m unittest discover -s tests -v
python demo.py
cd ..
```

That core demo checks protected source data, a reserved report input, and an
eligible debug log. Its paths differ from the shared-log browser demo. It
simulates owner approvals inside a new temporary fixture.

The [GitHub workflow](.github/workflows/browser-extension.yml) also runs a
Chromium integration test with a Slack page fixture and the actual local
executor. It saves browser screenshots as run artifacts. It does not use your
Slack account. Native Edge sidebar opening and the permission prompt still
need a manual check on your computer. See the
[extension testing guide](browser-extension/README.md#checks) for the local
Playwright command.

## Optional OpenRouter agents

The [feature branch](https://github.com/20SHA07/reFlex/tree/feat/browser-extension)
contains `test_agents/report_agent.py`, `test_agents/cleanup_agent.py`, and
`run_test_agents.py`. From a checkout of that branch:

```sh
python run_test_agents.py --show-report
python run_test_agents.py --openrouter --show-report
```

The first command uses offline samples. The second prompts for your OpenRouter
API key in the terminal and makes model calls. Neither command approves file
operations. Follow the branch's
[OpenRouter guide](https://github.com/20SHA07/reFlex/blob/feat/browser-extension/OPENROUTER_SETUP.md)
for its browser flow, model configuration, and three-file test expectations.

Keep the provider key in the Python backend. Only the separate local pairing
token goes into the extension. The Python OpenRouter path reads
`OPENROUTER_API_KEY` from the environment or prompts for it; it does not load
`.env` files. Event redemption codes are not API keys.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `browser_bridge.py` is missing | Check your directory and branch. Run from the repository root after updating `main`. |
| `--openrouter` is unrecognized | That option belongs to the OpenRouter feature branch, not the current main bridge. |
| `python` is unavailable | Try `python3` or `py`; Python 3.10+ is required. |
| The panel cannot find Slack | Open a channel on `app.slack.com` in the same browser window, reload Slack, then press **Refresh**. |
| The badge still says **Preview** | Complete local pairing; preview does not change files. |
| Pairing fails | Keep the bridge running, allow localhost access, use the current token, and check the ID on the reFlex extension card. IDs can change when loading a different folder. |
| Port `8765` is in use | Stop the older bridge with `Ctrl+C`, then start one bridge process. |
| A review is stale or the request timed out | Refresh the panel to check the current outcome. Do not assume a timed-out request made no changes. A changed file requires a fresh review; a paused fixture requires a restart. |
| Two sprites appear | Disable the standalone **The Ember for Slack** extension while testing the integrated reFlex extension. |

## Project layout and scope

| Path on `main` | Responsibility |
| --- | --- |
| `browser-extension/` | Side panel, Slack context, review controls, and integrated sprite |
| `browser_bridge.py` | Local pairing, disposable fixtures, task orchestration, and approval handling |
| `referee_agent/referee.py` | Deterministic protection and dependency rules |
| `referee_agent/executor.py` | Reviewed file operations, hash checks, quarantine, and audit logging |
| `sprite/` | Standalone renderer and animation development harness |
| `tests/` and `referee_agent/tests/` | Bridge and core referee checks |

Protection applies to operations sent through the controlled executor. reFlex
is not an operating-system sandbox and cannot stop unrelated programs from
editing files directly. Pairing identifies one local demo owner; Slack channel
context routes work but does not authenticate a Slack user. The extension does
not post messages to Slack. Approval state lives in one Python process and is
not restored from the audit journal after a restart.

For integration details, see the [browser contract](browser-extension/CONTRACT.md),
[bridge guide](browser-extension/BRIDGE.md), and
[referee integration guide](referee_agent/docs/referee_integration.md).

## Future integrations

Slack's website is the first environment. Possible next steps include adapters
for GitHub, Teams, other browser-based agent tools, and a local device companion.
These are future integrations; policy checks and execution authority should
remain on the trusted backend.

Built for **AI Tinkerers: Agents, Everywhere**.
