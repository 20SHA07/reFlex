# Truce Slack Channel

This app is the long-running managed CopilotKit Channels surface for Truce. CopilotKit Intelligence owns the Slack connection; this process owns the typed worker tools, SQLite state, approval checks, and controlled disposable-folder executor.

From the repository root:

```bash
npm ci
npm run referee:seed
npm run channel:setup -- --no-clipboard
npm run dev:slack
```

The setup command prints the maintained onboarding handoff. Complete the Slack installation and use verified scope/member identifiers in `.env`. A missing or unverified identity/scope is a fail-closed mutation block. Managed `ready()`/HTTP health is not proof of a real Slack reply; record live checks separately in `VALIDATION.md`.

The Channel uses the pinned `@copilotkit/channels@0.9.2` and `@copilotkit/runtime@1.70.3` pair, `identifyUser` from the platform adapter, Channels JSX cards, and a fresh `ChannelRunAgent` inner run per turn. Workplace MCP is disabled for Truce workers.

Useful commands:

```bash
npm run referee:demo-check
npm run referee:status
npm run referee:test
npm run typecheck --workspace channel
```

Supported mentions are `help`, `report: ...`, `cleanup: ...`, `status`, and the labelled `demo unsafe-cleanup` when demo mode and operator identity are configured.
