import { fileId, hashFile, requireWorkerFile, safeRelativePath } from "./paths";
import type { ProjectConfig, RefereeVerdict } from "./types";
import type { CleanupAction, Decision } from "./types";
import type { TruceStore } from "./store";

export async function evaluateCandidate(input: {
  action: CleanupAction;
  config: ProjectConfig;
  store: TruceStore;
}): Promise<RefereeVerdict> {
  const { action, config, store } = input;
  let safePath: string;
  try { safePath = safeRelativePath(action.relativePath); }
  catch { return blocked(action, "invalid_path", "The requested path is not a supported relative project file."); }

  if (safePath.startsWith("data/") || safePath.startsWith("reports/") || safePath === "data" || safePath === "reports") {
    return blocked(action, "protected_path", "Original source data and published reports are protected from cleanup.");
  }

  const blocking = store.dependenciesFor(config.projectId, true).filter((dependency) => dependency.relativePath === safePath);
  if (blocking.length > 0) {
    return {
      actionId: action.id, revision: action.revision, decision: "DEFER", reasonCode: "active_dependency",
      reason: `This file is still required by ${blocking.map((item) => item.taskId).join(", ")} until the report is published or cancelled.`,
      relativePath: safePath, blockingTaskIds: blocking.map((item) => item.taskId), requiredApproverId: action.requiredApproverId,
    };
  }

  try {
    const resolved = await requireWorkerFile(config.workspaceRoot, safePath);
    return {
      actionId: action.id, revision: action.revision, decision: "REVIEW", reasonCode: "eligible",
      reason: "This file is inside the disposable workspace, is not protected, and has no active dependency. The cleanup requester must approve the exact current file version.",
      relativePath: safePath, currentHash: await hashFile(resolved.absolutePath), blockingTaskIds: [], requiredApproverId: action.requiredApproverId,
    };
  } catch (error) {
    return blocked(action, "unavailable_target", error instanceof Error ? error.message : "The target cannot be inspected safely.");
  }
}

function blocked(action: CleanupAction, reasonCode: string, reason: string): RefereeVerdict {
  return { actionId: action.id, revision: action.revision, decision: "BLOCK", reasonCode, reason, relativePath: action.relativePath, blockingTaskIds: [], requiredApproverId: action.requiredApproverId };
}

export function decisionState(decision: Decision): CleanupAction["state"] {
  if (decision === "BLOCK") return "blocked";
  if (decision === "DEFER") return "deferred";
  if (decision === "REVIEW") return "pending_review";
  return "failed";
}

export function protectedPath(relativePath: string): boolean {
  return relativePath === "data" || relativePath === "reports" || relativePath.startsWith("data/") || relativePath.startsWith("reports/");
}

export { fileId };
