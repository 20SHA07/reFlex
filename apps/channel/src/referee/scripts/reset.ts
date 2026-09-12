import path from "node:path";
import { resetFixture } from "../fixtures";
import { repositoryRoot } from "../config";

const dataRoot = path.resolve(repositoryRoot, process.env.REFEREE_DATA_DIR || ".referee-data");
if (await import("node:fs/promises").then(({ access }) => access(path.join(dataRoot, "runtime.lock")).then(() => true, () => false))) throw new Error("Truce is running. Stop the backend before resetting the disposable fixture.");
const paths = await resetFixture(dataRoot);
console.log(`Created fresh Truce disposable run ${paths.runId}.`);
