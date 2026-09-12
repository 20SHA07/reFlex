import { access, lstat, readFile } from "node:fs/promises";
import path from "node:path";
import { activeRunPaths, assertMarkedRun, createDisposableRun, type RunPaths } from "./paths";
import { runPointerSchema } from "./schemas";

export async function seedFixture(dataRoot: string): Promise<RunPaths> {
  return createDisposableRun(dataRoot);
}

export async function resetFixture(dataRoot: string, requestedRunRoot?: string): Promise<RunPaths> {
  if (await access(path.join(dataRoot, "runtime.lock")).then(() => true, () => false)) throw new Error("Cannot reset while the Truce runtime is running.");
  if (requestedRunRoot) {
    const current = path.resolve(requestedRunRoot);
    const runsRoot = path.resolve(dataRoot, "runs");
    const relative = path.relative(runsRoot, current);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error("Reset may only select a run inside .referee-data/runs.");
    const markerPath = path.join(current, "workspace", ".truce-disposable-run.json");
    await assertMarkedRun({ runId: path.basename(current), dataRoot, workspaceRoot: path.join(current, "workspace"), privateRoot: path.join(current, "private"), dbPath: path.join(current, "private/truce.sqlite"), draftsRoot: path.join(current, "private/drafts"), quarantineRoot: path.join(current, "private/quarantine"), reportsRoot: path.join(current, "workspace/reports"), markerPath });
  }
  return seedFixture(dataRoot);
}

export async function currentRun(dataRoot: string): Promise<RunPaths> {
  const paths = await activeRunPaths(dataRoot);
  await assertMarkedRun(paths);
  return paths;
}

export async function fixtureCheck(dataRoot: string): Promise<{ ok: boolean; runId: string; problems: string[] }> {
  const problems: string[] = [];
  try {
    const pointer = runPointerSchema.parse(JSON.parse(await readFile(path.join(dataRoot, "active.json"), "utf8")));
    const paths = await currentRun(dataRoot);
    if (pointer.runId !== paths.runId) problems.push("active.json does not point at the marked run.");
    for (const relativePath of ["data/source_metrics.csv", "working/report_input.csv", "scratch/debug.log"]) {
      if (!(await lstat(path.join(paths.workspaceRoot, ...relativePath.split("/"))).then((info) => info.isFile(), () => false))) problems.push(`Missing ${relativePath}`);
    }
    return { ok: problems.length === 0, runId: paths.runId, problems };
  } catch (error) {
    problems.push(error instanceof Error ? error.message : "Fixture is not initialized.");
    return { ok: false, runId: "none", problems };
  }
}
