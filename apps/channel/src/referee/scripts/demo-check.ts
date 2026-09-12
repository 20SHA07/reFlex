import path from "node:path";
import { fixtureCheck } from "../fixtures";
import { loadProjectConfig, repositoryRoot } from "../config";

const dataRoot = path.resolve(repositoryRoot, process.env.REFEREE_DATA_DIR || ".referee-data");
const check = await fixtureCheck(dataRoot);
const config = await loadProjectConfig().catch(() => undefined);
const result = { ...check, scopeConfigured: Boolean(config && config.allowedWorkspaceKey && config.allowedChannelKey && config.allowedConversationKey && config.memberIds.size > 0), demoMode: config?.demoMode ?? false };
console.log(JSON.stringify(result, null, 2));
if (!check.ok) process.exitCode = 1;
