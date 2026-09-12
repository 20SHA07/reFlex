# Connect Agent Referee to Slack

Your first milestone is a reply to `/referee status` in a dedicated demo channel. Start with the sample backend, then connect the team's real workflows using the adapter described in `README.md`.

## 1. Create the app

1. Open [Your Slack apps](https://api.slack.com/apps) and choose **Create New App**, then **From a manifest**.
2. Select your hackathon workspace. Choose YAML and paste the complete contents of `slack_manifest.yaml`.
3. Review the configuration and create the app. If your workspace restricts app installation, its administrator will need to approve the installation.

The manifest enables one slash command, `/referee`, plus interactive buttons. Its only bot scopes are `commands` and `chat:write`. Slack documents this creation flow in [Configuring apps with app manifests](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/).

Socket Mode and Interactivity are already enabled. Leave request URLs unset: Slack delivers commands and button clicks through the app's outbound WebSocket connection. You do not need a public server or ngrok for this runner. See [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/) and the [manifest reference](https://docs.slack.dev/reference/app-manifest/).

## 2. Add two tokens locally

In the app settings:

1. Open **OAuth & Permissions**, install the app to your workspace, and copy the **Bot User OAuth Token**. It starts with `xoxb-`.
2. Open **Basic Information**, find **App-Level Tokens**, and choose **Generate Token and Scopes**. Give it a name such as `referee-local`, add `connections:write`, and generate it. This token starts with `xapp-`. The [connections:write scope](https://docs.slack.dev/reference/scopes/connections.write/) belongs on this app-level token, not in the bot scope list.
3. In your local project folder, copy `.env.example` to `.env` and edit the values in your editor:

   ```dotenv
   SLACK_BOT_TOKEN=xoxb-your-bot-token
   SLACK_APP_TOKEN=xapp-your-app-token
   SLACK_TEAM_ID=
   ```

Keep both tokens in `.env`; do not send them in chat, commit them, or include them in your demo recording. Both must belong to this same app. `SLACK_TEAM_ID` is optional; leave it blank initially, or set the intended workspace ID to restrict incoming requests to that workspace.

## 3. Run the Slack interface

Open a terminal in this project folder. Use Python 3.10 or newer.

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-slack.txt
python run_slack.py --preview
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-slack.txt
.venv\Scripts\python.exe run_slack.py --preview
```

Keep the terminal running and your computer connected to the internet. The runner loads `.env` automatically.

**Preview mode connects to real Slack and posts clearly labelled sample cards.** It uses sample state in memory, makes no AI calls, and does not operate on project files. Preview state resets when the process restarts. Use it to finish your Slack integration while teammates build the real referee.

## 4. Get your first reply

1. Create or open a dedicated channel, such as `#agent-referee-demo`.
2. In that channel, run `/invite @Agent Referee` and select your app.
3. Send `/referee status`. You should receive a card marked as a preview.
4. Try the remaining commands and their approval buttons:

   | Command | Result |
   |---|---|
   | `/referee status` | Current report and cleanup state |
   | `/referee report` | Report workflow and any report approval available |
   | `/referee cleanup` | Protected, deferred, and eligible files, plus eligible cleanup approval |

The UI passes the actual Slack user, channel, and workspace to the backend. The real backend must authorize the user and recheck state before executing an approval.

## 5. Connect the real workflow

Stop the preview process with Ctrl+C. Follow `README.md` to implement the backend contract, then select the team's factory explicitly:

```bash
python run_slack.py --backend your_adapter:create_backend
```

Replace `your_adapter:create_backend` with the actual importable module and factory. Preview mode is for interface testing; the real adapter is required to run agents, publish a report, enforce file protection, and quarantine files.

## If something fails

| Symptom | Check |
|---|---|
| `/referee` is missing | Confirm that this app is installed in the workspace you are using and its Slash Commands settings list `/referee`. |
| `dispatch_failed`, timeout, or no reply | Keep one runner active, confirm Socket Mode is on, and check the terminal for a connection or backend error. Outbound Slack WebSocket access must be available. |
| `not_in_channel` or `channel_not_found` | Invite the app into the channel where you issue commands. For a private channel, add the app from that channel. |
| `missing_scope` on a message | Check `commands` and `chat:write` under Bot Token Scopes, then reinstall the app after changing scopes. |
| `missing_scope` when connecting | Generate an app-level `xapp-` token with `connections:write`. |
| `invalid_auth`, `not_authed`, or `token_revoked` | Check the two variable names and token prefixes. Replace revoked tokens, save `.env`, and restart. Do not paste tokens into a support message. |
| Buttons do nothing | Check Interactivity is enabled and the runner is still connected. Inspect the terminal for the action error. |
| Approval says unavailable or expired after restart | Request a fresh report or cleanup card. Preview state and UI approval references are not persistent across restarts. |
| Changes show up inconsistently | Stop extra local runners connected to this same app. Use one process for the demo. |

For debugging, share the error code and the command you tried, with credentials and sensitive project data removed.
