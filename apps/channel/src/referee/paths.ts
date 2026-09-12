import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { access, copyFile, lstat, mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { runPointerSchema, seedMarkerSchema } from "./schemas";

export type RunPaths = {
  runId: string;
  dataRoot: string;
  workspaceRoot: string;
  privateRoot: string;
  dbPath: string;
  draftsRoot: string;
  quarantineRoot: string;
  reportsRoot: string;
  markerPath: string;
};

export const REPORT_INPUT = "working/report_input.csv";
export const SOURCE_METRICS = "data/source_metrics.csv";
export const DEBUG_LOG = "scratch/debug.log";

export function fileId(relativePath: string): string {
  return `file:${safeRelativePath(relativePath)}`;
}

export function safeRelativePath(relativePath: string): string {
  if (
    !relativePath ||
    relativePath.includes("\0") ||
    relativePath.includes("\\") ||
    path.posix.isAbsolute(relativePath) ||
    path.win32.isAbsolute(relativePath) ||
    /^[A-Za-z]:/.test(relativePath)
  ) {
    throw new Error("Path is not a supported relative project path.");
  }
  const normalized = path.posix.normalize(relativePath);
  if (normalized === "." || normalized === ".." || normalized.startsWith("../") || normalized.includes("/../")) {
    throw new Error("Path traversal is not allowed.");
  }
  return normalized;
}

export function assertWithin(root: string, candidate: string): void {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("Path is outside the configured project boundary.");
  }
}

async function assertNoSymlink(root: string, candidate: string): Promise<void> {
  assertWithin(root, candidate);
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  let current = path.resolve(root);
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    try {
      const info = await lstat(current);
      if (info.isSymbolicLink()) throw new Error("Symlink targets are not allowed.");
    } catch (error) {
      const code = error instanceof Error && "code" in error ? error.code : undefined;
      if (code !== "ENOENT") throw error;
    }
  }
}

export async function resolveWorkerFile(workspaceRoot: string, relativePath: string, required = true) {
  const safePath = safeRelativePath(relativePath);
  const absolutePath = path.resolve(workspaceRoot, ...safePath.split("/"));
  await assertNoSymlink(workspaceRoot, absolutePath);
  try {
    const info = await lstat(absolutePath);
    if (!info.isFile()) throw new Error("Only regular files may be used.");
    if (info.nlink > 1) throw new Error("Hard-linked targets are not supported in the disposable workspace.");
    if (info.size > 1024 * 1024) throw new Error("File exceeds the 1 MiB workspace limit.");
    return { safePath, absolutePath, info };
  } catch (error) {
    if (!required && error instanceof Error && "code" in error && error.code === "ENOENT") return undefined;
    throw error;
  }
}

export async function requireWorkerFile(workspaceRoot: string, relativePath: string) {
  const resolved = await resolveWorkerFile(workspaceRoot, relativePath, true);
  if (!resolved) throw new Error(`Required project file is missing: ${relativePath}`);
  return resolved;
}

export async function hashFile(absolutePath: string): Promise<string> {
  const bytes = await readFile(absolutePath);
  return createHash("sha256").update(bytes).digest("hex");
}

export async function verifyHash(absolutePath: string, expectedHash: string): Promise<boolean> {
  try {
    return (await hashFile(absolutePath)) === expectedHash;
  } catch {
    return false;
  }
}

export async function ensureDirectory(directory: string): Promise<void> {
  await mkdir(directory, { recursive: true });
}

export function runPaths(dataRoot: string, runId: string): RunPaths {
  const runRoot = path.join(dataRoot, "runs", runId);
  const workspaceRoot = path.join(runRoot, "workspace");
  const privateRoot = path.join(runRoot, "private");
  return {
    runId,
    dataRoot,
    workspaceRoot,
    privateRoot,
    dbPath: path.join(privateRoot, "truce.sqlite"),
    draftsRoot: path.join(privateRoot, "drafts"),
    quarantineRoot: path.join(privateRoot, "quarantine"),
    reportsRoot: path.join(workspaceRoot, "reports"),
    markerPath: path.join(workspaceRoot, ".truce-disposable-run.json"),
  };
}

export async function activeRunPaths(dataRoot: string): Promise<RunPaths> {
  const pointerPath = path.join(dataRoot, "active.json");
  try {
    const pointer = runPointerSchema.parse(JSON.parse(await readFile(pointerPath, "utf8")));
    return runPaths(dataRoot, pointer.runId);
  } catch (error) {
    const code = error instanceof Error && "code" in error ? error.code : undefined;
    if (code !== "ENOENT") throw new Error(`Invalid Truce active run pointer: ${String(error)}`);
    return runPaths(dataRoot, "default");
  }
}

export async function assertMarkedRun(paths: RunPaths): Promise<void> {
  const marker = seedMarkerSchema.parse(JSON.parse(await readFile(paths.markerPath, "utf8")));
  if (marker.runId !== paths.runId) throw new Error("Disposable run marker does not match the selected run.");
}

export async function createDisposableRun(dataRoot: string, runId = `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`): Promise<RunPaths> {
  if (!/^run-[a-z0-9-]+$/.test(runId)) throw new Error("Invalid disposable run id.");
  const paths = runPaths(dataRoot, runId);
  await ensureDirectory(path.join(paths.workspaceRoot, "data"));
  await ensureDirectory(path.join(paths.workspaceRoot, "working"));
  await ensureDirectory(path.join(paths.workspaceRoot, "scratch"));
  await ensureDirectory(paths.reportsRoot);
  await ensureDirectory(paths.draftsRoot);
  await ensureDirectory(paths.quarantineRoot);
  await writeFile(
    paths.markerPath,
    JSON.stringify({ marker: "truce-disposable-run", runId, createdAt: new Date().toISOString() }, null, 2),
  );
  await writeFile(path.join(paths.workspaceRoot, "data/source_metrics.csv"),
    "period,revenue_aed,closed_deals,open_tickets\n2026-08,120000,12,20\n2026-09,150000,15,16\n");
  await copyFile(path.join(paths.workspaceRoot, "data/source_metrics.csv"), path.join(paths.workspaceRoot, REPORT_INPUT));
  await writeFile(path.join(paths.workspaceRoot, DEBUG_LOG), "2026-09-12T00:00:00Z synthetic debug: fixture ready\n2026-09-12T00:00:01Z synthetic debug: no credentials\n");
  await ensureDirectory(dataRoot);
  await writeFile(path.join(dataRoot, "active.json"), JSON.stringify({ runId }, null, 2));
  return paths;
}

export async function ensureRunReady(paths: RunPaths): Promise<void> {
  await assertMarkedRun(paths);
  await access(paths.workspaceRoot, constants.R_OK);
  await access(paths.privateRoot, constants.R_OK | constants.W_OK);
}

export async function moveSameFilesystem(source: string, destination: string): Promise<void> {
  if (path.parse(source).root !== path.parse(destination).root) throw new Error("Cross-filesystem moves are refused.");
  await rename(source, destination);
}

export async function fileInfo(absolutePath: string) {
  const info = await stat(absolutePath);
  return { size: info.size, modifiedAt: info.mtime.toISOString() };
}
