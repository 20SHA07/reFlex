# Truce submission materials

## Title

Truce — Coordinate agents. Protect shared work.

## Description

Truce coordinates participating AI workers inside a team's Slack conversation. A report worker holds its working CSV dependency through owner review. A cleanup proposal is evaluated item by item: protected source data is blocked, active report input is deferred, and unrelated disposable files are offered for the cleanup requester's approval. Approved cleanup moves a hash-verified file into quarantine. Verified report publication releases the dependency and creates a new review revision; it never grants automatic cleanup permission.

CopilotKit Channels provides the Slack surface and native cards. The configured OpenAI/OpenRouter model drives the supported worker tool calls. TypeScript policy and a SQLite-backed executor perform all relevant checks and mutations.

## Provenance

Inherited infrastructure: the official CopilotKit starter lifecycle, managed Channels integration, `BuiltInAgent`/provider resolver, `ChannelRunAgent` reentry adapter, package versions, and Channels JSX runtime.

Built for Truce: dependency registry, typed identity envelope, SQLite migrations/store, deterministic referee, ownership and approval revisions, bounded path checks, quarantine/publication journal, recovery, fixtures, report metrics, cards, worker prompts/tools, tests, runbook, and demo materials.

## Claims boundary

This local P0 build supports one configured disposable project and one managed Slack path. It does not sandbox arbitrary local processes, intercept unrelated agents, permanently delete files, provide distributed transactions, or ship P1 durable callback reconstruction/snapshot negotiation. Repository publication, social posting, and portal submission are not performed by Codex.

The official event deadline and partner handles are intentionally not guessed. Complete the local organizer's current checklist before submitting.
