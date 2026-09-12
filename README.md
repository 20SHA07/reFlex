# reFlex

Agent referee for coordinating agent work and protecting important files through
a controlled executor.

The browser interface is a **Chrome extension used beside Slack's website**.
Load `browser-extension/` as an unpacked extension, then open its side panel.
Start with the [Windows AI agents setup](AI_AGENTS.md), or the
[extension setup](browser-extension/README.md) for the interface alone.

For a complete local demo with real quarantine, report publication, and protected
file checks, follow the [bridge setup](browser-extension/BRIDGE.md). Reports use
fixed sample data by default. Start `browser_bridge.py --live` to use the
OpenRouter Report Agent and Cleanup Agent on those disposable fixtures. Provider
credentials stay in the Python process. The existing referee still controls
publication and quarantine after review.

`browser_bridge.py` is the runnable application entry point; a separate
`main.py` is not needed for this local demo. `agents.py` and `prompts/` implement
the two workers and a constrained explanation helper. `demo_agents.py` runs
them independently, and `diagnose_openrouter.py` isolates connection, key, and
model errors. See [AI_AGENTS.md](AI_AGENTS.md) for exact commands.

This version retains the latest shared-log scenario: the report reserves
`logs/agent_activity.log`, cleanup waits, and approved publication creates a
fresh cleanup review. A separate approval quarantines the log.

The team's referee and executor are in `referee_agent/`. Their
[integration guide](referee_agent/docs/referee_integration.md) describes the
controlled file operations.
