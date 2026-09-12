# reFlex browser extension for Slack

A Chromium browser side panel for reviewing agent conflicts while a Slack channel is open.
The extension uses the current workspace/channel to keep reviews together. It
imports selected text only when you press **Use selection**.

## Load it now

1. Download or check out the `feat/browser-extension` branch of this repository.
2. Open `edge://extensions` in current Edge, or `chrome://extensions` in Chrome
   116 or newer, and enable **Developer mode**.
3. Click **Load unpacked** and select this `browser-extension` directory, the one
   containing `manifest.json`. No JavaScript build or package installation is needed.
4. Reload Slack's website and open a channel at `https://app.slack.com/client/...`.
5. Pin **reFlex · Agent Referee** from your browser's Extensions menu, then click it to
   open the side panel. It starts in **Preview**, with no file changes.

Slack's desktop application does not load browser extensions. Use Slack in Edge
or Chrome. Native Edge opening and permission prompts still need a manual check.

## Rehearse the demo

1. Press **Start report**. The report agent holds its working input.
2. Press **Review cleanup**. Source data is **BLOCK**, the report input is
   **DEFER**, and the debug log is **REVIEW**.
3. Approve the debug log's quarantine.
4. Read the report draft, then approve publication. Its input dependency releases.
5. The input now has a **fresh** cleanup proposal. Approve it separately.

Switch Slack channels to see separate review state. Switching channels also
invalidates the panel's currently displayed controls until context refreshes.
An old approval cannot act on the new channel.

For real file operations, follow [BRIDGE.md](BRIDGE.md). Start the Python bridge,
copy its pairing token into **Connect local demo**, and allow localhost access.
The existing executor then operates on newly created disposable sample files.
No Slack bot token or model key is needed for the offline sample workflow.
For real OpenRouter calls and a terminal test of both agents, follow
[OPENROUTER_SETUP.md](../OPENROUTER_SETUP.md). Provider keys stay on the backend.

## What is implemented

- Manifest V3 extension, native side panel, selected-text import, channel routing.
- Per-file decisions, exact draft approval, immutable review IDs, fresh approvals
  after dependency release, activity history, and retry handling.
- Preview workflow and a paired HTTP bridge to the team's real controlled executor.
- Two test agents: Report Agent drafts a client update; Cleanup Agent proposes
  quarantine of the demo candidates. Both support OpenRouter or offline samples.
- Model-generated reasons are shown separately from the referee's decision.
  No model can approve a file operation.

File protection applies to actions routed through the executor. A browser
extension cannot intercept arbitrary agent shell commands or protect the whole
computer. Pairing represents a local demo owner, not an authenticated Slack user.
The extension does not post to Slack or read Slack tokens, cookies, or history.

## Team integration

Keep provider credentials and filesystem authority on the backend. The browser
only submits requests and approves server-issued proposals. See
[CONTRACT.md](CONTRACT.md) for messages and response shapes and
[BRIDGE.md](BRIDGE.md) for the local service. A production host would need verified
user authentication and explicit project authorization.

The CopilotKit starter kit can help the backend team, but no Slack app installation
or hosted Channels service is required to load this browser extension.

## Checks

From the repository root, with Node 22+ and Python 3.10+:

```sh
node --test browser-extension/tests/*.test.mjs
python3 -m unittest discover -s tests -v
```

The GitHub workflow also launches Chromium, loads an extension test copy, serves
a local Slack-shaped page at a routed Slack URL, and exercises both preview and
the real bridge. No Slack account or messages are used. It pre-grants localhost
permission and opens the panel as an extension page for automation. Check native
toolbar opening, side-panel placement, and the permission prompt manually after
loading the unmodified extension.

To run the browser check locally:

```sh
cd browser-extension
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node tests/browser-smoke.mjs
```

Close the manual bridge first so the test can use port 8765. Screenshot artifacts
show the preview and local demo. Test dependencies are not needed by the extension.
