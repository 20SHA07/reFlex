import { loadProjectConfig } from "../config";
import { TruceCoordinator } from "../coordinator";

const config = await loadProjectConfig();
const coordinator = await TruceCoordinator.open(config);
try {
  const snapshot = await coordinator.status();
  console.log(JSON.stringify({ ...snapshot, config: { projectId: config.projectId, workspaceRoot: config.workspaceRoot, policyVersion: config.policyVersion } }, null, 2));
} finally { coordinator.close(); }
