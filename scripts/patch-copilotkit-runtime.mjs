import { access, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// @copilotkit/runtime@1.70.3 currently publishes the v2 transcription-service
// source map and CJS build but omits its ESM file, even though v2/index.mjs
// imports it. Keep the pinned Channels/runtime pair and repair only this
// declaration-only class at install time so managed Channels can load.
const target = path.resolve("node_modules/@copilotkit/runtime/dist/v2/runtime/transcription-service/transcription-service.mjs");
try { await access(target); }
catch {
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, 'import "reflect-metadata";\nexport class TranscriptionService {}\n');
}
