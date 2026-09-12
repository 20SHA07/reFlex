/**
 * There is no `channel.start()`. Attaching the Channel to a CopilotRuntime and
 * creating the listener is what starts it — which is why teardown is wired
 * before the listener exists.
 */
import { createServer } from "node:http";
import { access, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { CopilotKitIntelligence, CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotNodeListener } from "@copilotkit/runtime/v2/node";
import { channel } from "./channel";
import { required } from "./env";
import { getCoordinator, initializeCoordinator } from "./referee/runtime";

const intelligence = new CopilotKitIntelligence({
  apiKey: required("INTELLIGENCE_API_KEY"),
  // Hosted Intelligence supplies both defaults. Override both together only for
  // self-hosted — they are separate hosts, so never derive one from the other.
  apiUrl: process.env.INTELLIGENCE_API_URL,
  wsUrl: process.env.INTELLIGENCE_GATEWAY_WS_URL,
});

const coordinator = await initializeCoordinator();
await access(path.join(coordinator.config.workspaceRoot, ".truce-disposable-run.json"));
const runtimeLock = path.join(coordinator.config.dataRoot, "runtime.lock");
await writeFile(runtimeLock, JSON.stringify({ processSession: coordinator.config.processSession, startedAt: new Date().toISOString() }), { flag: "wx" }).catch((error: unknown) => {
  throw new Error(`A Truce runtime lock is already active. Stop the existing process before starting another. ${String(error)}`);
});

const runtime = new CopilotRuntime({
  agents: {}, // required even though the Channel supplies the agent
  intelligence,
  channels: [channel],
});

let teardown: (() => Promise<void>) | undefined;
const shutdown = async () => {
  await teardown?.();
  process.exit(0);
};
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

const listener = createCopilotNodeListener({ runtime, basePath: "/api/copilotkit" });
const channels = listener.channels;
const server = createServer(listener);

teardown = async () => {
  await channels.stop();
  if (server.listening) server.close();
  await unlink(runtimeLock).catch(() => undefined);
  coordinator.close();
};

await channels.ready({ timeoutMs: 30_000 });

// `ready()` is NOT proof of life — it resolves on `setup_required` too, because
// a declared-but-unprovisioned Channel counts as a valid degraded state. Skip
// this check and you get a process that boots cleanly, serves 200s, and answers
// nothing.
const status = channels.status();
if (status.overall !== "online") {
  console.error(
    `\n  Channel is not online: ${JSON.stringify(status)}\n` +
      `  → 'setup_required' means the provider side is unfinished. Run: npm run channel:status\n` +
      `  → See dev-docs/troubleshooting.md\n`,
  );
  await teardown();
  process.exit(1);
}

const port = Number(process.env.PORT ?? 3000);
server.listen(port, () => {
  console.log(`\n  ✓ Channel "${process.env.CHANNEL_CODE}" online — listening on :${port}`);
  console.log(`    Invite the bot to a channel (/invite @yourbot), then @-mention it.\n`);
});
