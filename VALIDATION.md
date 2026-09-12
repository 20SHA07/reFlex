# Truce validation record

## Offline checks

| Check | Result | Evidence |
| --- | --- | --- |
| SQLite schema, fixture seed, and status | PASS | `npm run referee:seed`, `npm run referee:status` |
| P0 domain acceptance tests | PASS | `npm run referee:test` — 8 tests passed against temporary SQLite and real files |
| Channel typecheck | PASS | `npm run typecheck --workspace channel` |
| Full workspace typecheck | NOT RUN TO GREEN | Starter web workspace has unrelated voice-template type errors; channel and agent-core typechecks pass |
| Full `npm run verify` | NOT RUN TO GREEN | Same inherited web-template type errors; no Truce test is skipped by `referee:test` |

## Live managed checks

All live checks below are NOT RUN in this workspace. They require a human to complete CopilotKit Intelligence sign-in, create/connect the managed Slack Channel, install it into an authorized workspace, and provide verified workspace/channel/thread scope and canonical member IDs in `.env`.

- Managed Channel connects and replies in the intended Slack thread: NOT RUN.
- Earlier thread context is used without replaying quoted instructions: NOT RUN.
- Alex and Sam resolve to different canonical platform identities: NOT RUN.
- Native button click reaches the backend with the actual clicker's identity: NOT RUN.
- Wrong-user approval/publication rejection in live Slack: NOT RUN.
- Real model/tool report and normal cleanup runs: NOT RUN.
- Labelled unsafe proposal through the real Slack cards: NOT RUN.
- Saved report, quarantine receipt, source preservation, and local status evidence: offline PASS; live card path NOT RUN.
- Publication followed by fresh deferred cleanup approval: offline PASS; live path NOT RUN.
- Second live rehearsal from a fresh fixture: NOT RUN.

The managed SDK's typed tool/interaction context does not expose workspace/channel/thread scope fields in this installed version. Truce therefore rejects live mutations when those verified scope values are unavailable instead of granting broad approval. Use a setup/API version that supplies those fields through the trusted adapter, then repeat the live checks.
