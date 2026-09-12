# reFlex

Agent referee for coordinating agent work and protecting important files through
a controlled executor.

The browser interface is a **Chrome extension used beside Slack's website**.
Load `browser-extension/` as an unpacked extension, then open its side panel.
Start with the [extension setup](browser-extension/README.md).

For a complete local demo with real quarantine, report publication, and protected
file checks, follow the [bridge setup](browser-extension/BRIDGE.md). Two test
agents can use OpenRouter for report drafting and cleanup proposals. Follow the
[OpenRouter setup](OPENROUTER_SETUP.md) to enable them or test the pair directly.
Without `--openrouter`, they use offline sample output.

The team's referee and executor are in `referee_agent/`. Their
[integration guide](referee_agent/docs/referee_integration.md) describes the
controlled file operations.
