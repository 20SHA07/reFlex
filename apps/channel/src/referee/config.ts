import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { activeRunPaths, type RunPaths } from "./paths";
import type { ProjectConfig } from "./types";

export const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");

function csvSet(value: string | undefined): Set<string> {
  return new Set((value ?? "").split(",").map((item) => item.trim()).filter(Boolean));
}

function positiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number(value ?? fallback);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export async function loadProjectConfig(env: NodeJS.ProcessEnv = process.env, root = env.REFEREE_REPO_ROOT || repositoryRoot): Promise<ProjectConfig> {
  const dataRoot = path.resolve(root, env.REFEREE_DATA_DIR || ".referee-data");
  const paths = await activeRunPaths(dataRoot);
  return configFromPaths(paths, env);
}

export function configFromPaths(paths: RunPaths, env: NodeJS.ProcessEnv = process.env): ProjectConfig {
  return {
    projectId: env.REFEREE_PROJECT_ID || "client-update-demo",
    workspaceRoot: paths.workspaceRoot,
    privateRoot: paths.privateRoot,
    dbPath: paths.dbPath,
    draftsRoot: paths.draftsRoot,
    quarantineRoot: paths.quarantineRoot,
    reportsRoot: paths.reportsRoot,
    dataRoot: paths.dataRoot,
    policyVersion: "truce-p0-v1",
    allowedWorkspaceKey: env.REFEREE_ALLOWED_WORKSPACE_KEY || "",
    allowedChannelKey: env.REFEREE_ALLOWED_CHANNEL_KEY || "",
    allowedConversationKey: env.REFEREE_ALLOWED_CONVERSATION_KEY || "",
    memberIds: csvSet(env.REFEREE_MEMBER_IDS),
    demoOperatorIds: csvSet(env.REFEREE_DEMO_OPERATOR_IDS),
    demoMode: env.REFEREE_DEMO_MODE === "true",
    approvalTtlSeconds: positiveInt(env.REFEREE_APPROVAL_TTL_SECONDS, 900),
    modelTimeoutMs: positiveInt(env.REFEREE_MODEL_TIMEOUT_MS, 60_000),
    maxToolSteps: Math.min(10, positiveInt(env.REFEREE_MAX_TOOL_STEPS, 10)),
    processSession: randomUUID(),
  };
}

export function isPlaceholder(value: string): boolean {
  return !value || value.startsWith("<") || value === "stub-replace-me";
}

export function localConfigFromPaths(paths: RunPaths, overrides: Partial<ProjectConfig> = {}): ProjectConfig {
  return {
    ...configFromPaths(paths, {}),
    allowedWorkspaceKey: "workspace-test",
    allowedChannelKey: "channel-test",
    allowedConversationKey: "conversation-test",
    memberIds: new Set(["slack:alex", "slack:sam"]),
    demoOperatorIds: new Set(["slack:operator"]),
    demoMode: true,
    ...overrides,
  };
}
