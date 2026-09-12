# reFlex — Agent Referee

**A browser extension that coordinates AI agents before they break each other’s work.**

reFlex sits beside Slack in Chrome and reviews sensitive agent actions in context. When one agent wants to change or remove a file, the referee checks whether that action conflicts with protected data, another active task, a stale file version, or a required human approval.

The result is surfaced through an on-screen referee sprite:

- 🟢 **Green** — the action is allowed
- 🟡 **Yellow** — the action is deferred or needs review
- 🔴 **Red** — the action is blocked

Rather than acting like a standalone chatbot, reFlex uses the environment around the agents — the current Slack workspace/channel, active task dependencies, file state, and approval history — to decide what should happen next.

## Demo scenario

The prototype focuses on one clear conflict:

1. A **report agent** starts work and reserves `logs/agent_activity.log` as an active dependency.
2. A **cleanup agent** proposes removing that same log.
3. reFlex returns **DEFER** because another active task still needs the file.
4. The user reviews and approves the report.
5. The dependency is released and the cleanup request is re-evaluated with a **new action ID**.
6. The user can approve the fresh cleanup request, which moves the file to quarantine rather than permanently deleting it.

This demonstrates the core idea: an action can look safe in isolation and still be unsafe in context.

## How it works

```text
Slack in Chrome
     │
     ▼
reFlex browser extension
(side panel + referee sprite)
     │
     ▼
Local browser bridge
     │
     ▼
Agent Referee
├─ deterministic policy checks
├─ active task dependencies
├─ file hash / stale-state checks
├─ approval validation
└─ audit logging
     │
     ▼
Controlled executor
     │
     ├─ publish approved report
     └─ quarantine approved cleanup
```

The browser never receives unrestricted filesystem authority. It submits requests and approvals to a local bridge, which routes them through the controlled referee/executor.

## Safety model

The referee core makes deterministic decisions before execution:

- **BLOCK** — protected path, unsupported operation, invalid path, or unsafe filesystem condition
- **DEFER** — another active task still depends on the file
- **REVIEW** — the action is eligible but requires human approval
- **ALLOW** — the exact reviewed action passed final checks and executed

Before executing an approved action, reFlex checks the current state again. If the file changed, a new dependency appeared, the action was already used, or the review became stale, execution is prevented.

Cleanup is implemented as **quarantine**, not permanent deletion.

## Browser extension

The Chrome extension is built with **Manifest V3** and uses a native **side panel** while Slack is open in the browser.

Implemented browser features include:

- Slack workspace/channel-aware review state
- selected-text import only when the user explicitly requests it
- review cards with action status and explanations
- referee sprite with green, yellow, and red cards
- immutable review IDs and fresh approvals after dependency release
- local bridge pairing for real demo file operations
- retry and stale-context handling

The extension does **not** read Slack tokens, cookies, or message history, and it does not require a Slack bot installation.

## Quick start

### 1. Load the extension

Requirements: **Chrome 116+**.

1. Clone this repository.
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Select **Load unpacked**.
5. Choose the `browser-extension/` directory.
6. Open Slack in Chrome at `https://app.slack.com/client/...`.
7. Pin **reFlex · Agent Referee** and open the side panel.

The extension starts in **Preview** mode, so no files are changed.

For extension-specific instructions, see [`browser-extension/README.md`](browser-extension/README.md).

### 2. Connect the real local demo

Requirements: **Python 3.10+**.

From the repository root:

```bash
python3 browser_bridge.py --extension-id YOUR_EXTENSION_ID
```

The bridge prints the location of a temporary pairing token. Paste that token into **Connect local demo** in the extension, allow localhost access, and run the report/cleanup scenario.

The bridge listens only on `127.0.0.1:8765` and creates disposable sample files for the demo.

Full setup: [`browser-extension/BRIDGE.md`](browser-extension/BRIDGE.md).

## Repository structure

```text
reFlex/
├── browser-extension/       # Chrome side panel, Slack adapter, sprite UI, browser tests
├── referee_agent/           # deterministic policy engine + controlled executor
│   ├── referee.py
│   ├── executor.py
│   ├── demo.py
│   ├── docs/
│   └── tests/               # 30 safety and coordination tests
├── browser_bridge.py        # paired localhost bridge to the real executor
├── tests/                   # bridge integration tests
└── .github/workflows/       # browser + bridge CI
```

## Testing

The referee core includes **30 safety and coordination tests** covering protected paths, active dependencies, stale state, authorization, duplicate execution, concurrency, path traversal, symlinks, hardlinks, quarantine behavior, and failure handling.

Run the core tests:

```bash
cd referee_agent
python -m unittest discover -s tests -v
```

Run extension and bridge checks from the repository root:

```bash
node --test browser-extension/tests/*.test.mjs
python3 -m unittest discover -s tests -p 'test_browser_bridge.py' -v
```

The GitHub Actions workflow also installs Chromium with Playwright and runs an end-to-end browser smoke test against the real local executor.

## Trust boundary

reFlex protects actions that are routed through its controlled executor. It is **not** an operating-system sandbox and cannot stop an unrelated process or an agent with unrestricted shell/filesystem access from modifying files directly.

The current bridge is intentionally a local hackathon prototype:

- pairing represents one local demo owner rather than verified Slack identity
- workspace/channel IDs are routing context, not authentication
- the report generator is currently a deterministic sample, not a live model-generated report
- task/action state is single-process and in memory
- provider credentials should remain on a trusted backend in future integrations

These boundaries are deliberate: the prototype demonstrates the coordination and enforcement path without claiming system-wide control it does not have.

## Why reFlex

Most agent safety controls evaluate one agent at a time. reFlex focuses on a different failure mode: **multiple useful agents taking actions that are individually reasonable but mutually incompatible**.

By putting the referee inside the environment where the work is happening, users get immediate, visual, contextual decisions instead of generic warnings after the fact.

## Roadmap

The current prototype uses Slack in Chrome as the first environment. The same referee model can be extended through environment-specific adapters for GitHub, Teams, browser-based agent tools, and a local device companion — while keeping policy checks and execution authority on a trusted backend.

---

Built for **AI Tinkerers — Agents, Everywhere**.
