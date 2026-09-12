# reFlex browser extension for Slack

A Chrome side panel for reviewing agent conflicts while a Slack channel is open.
The extension uses the current workspace/channel to keep reviews together. It
imports selected text only when you press **Use selection**.

The Slack page also has a click-through referee sprite. The service worker sends
only trusted, current referee state to it after a dispatch: `BLOCK` presents a
red card, `DEFER` or `REVIEW` a yellow card, and `ALLOW` a green card. The
sprite never contacts the bridge or makes approval decisions.

For a visual rehearsal on Slack: `Alt+Shift+E` shows green, `Alt+Shift+W`
shows yellow, and `Alt+Shift+R` shows red.

## Load it now

1. Download or check out the `feat/browser-extension` branch of this repository.
2. Open `chrome://extensions` in Chrome 116 or newer and enable **Developer mode**.
3. Click **Load unpacked** and select this `browser-extension` directory, the one
   containing `manifest.json`. No JavaScript build or package installation is needed.
4. Reload Slack's website and open a channel at `https://app.slack.com/client/...`.
5. Pin **reFlex · Agent Referee** from Chrome's Extensions menu, then click it to
   open the side panel. It starts in **Preview**, with no file changes.

Slack's desktop application does not load Chrome extensions. Use Slack in Chrome.

## Rehearse the demo

1. Press **Start report**. The report agent reserves `logs/agent_activity.log`.
2. Press **Review cleanup**. The cleanup agent requests deletion of that same
   log, and the referee returns **DEFER** because the report is still using it.
3. Read the report draft, then approve publication.
4. The referee releases the log and creates a **fresh** cleanup review.
5. Approve that fresh review to move the log to quarantine (never permanent deletion).

Switch Slack channels to see separate review state. Switching channels also
invalidates the panel's currently displayed controls until context refreshes.
An old approval cannot act on the new channel.

For real file operations, follow [BRIDGE.md](BRIDGE.md). Start the Python bridge,
copy its pairing token into **Connect local demo**, and allow localhost access.
The existing executor then operates on newly created disposable sample files.
No Slack bot token, OpenRouter key, or sponsor offer is needed for this demo.

## What is implemented

- Manifest V3 extension, native side panel, selected-text import, channel routing.
- Per-file decisions, exact draft approval, immutable review IDs, fresh approvals
  after dependency release, activity history, and retry handling.
- Preview workflow and a paired HTTP bridge to the team's real controlled executor.
- Fixed sample reports. Connecting your team's model agents is the next backend
  integration; the sample report is explicitly labelled throughout the interface.

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
python3 -m unittest discover -s tests -p 'test_browser_bridge.py' -v
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
