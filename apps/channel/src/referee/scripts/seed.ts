import path from "node:path";
import { seedFixture } from "../fixtures";
import { TruceStore } from "../store";
import { configFromPaths, repositoryRoot } from "../config";

const dataRoot = path.resolve(repositoryRoot, process.env.REFEREE_DATA_DIR || ".referee-data");
const paths = await seedFixture(dataRoot);
const config = configFromPaths(paths);
const store = await TruceStore.open(config.dbPath);
store.ensureProject(config);
store.close();
console.log(`Seeded Truce disposable run ${paths.runId}.`);
console.log(`Workspace: ${paths.workspaceRoot}`);
