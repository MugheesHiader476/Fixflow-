/** Regenerate Next route types before tsc: stale per-route validators from removed pages otherwise fail typecheck. */
import { rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
for (const stale of [".next/types", ".next/dev/types"]) rmSync(resolve(root, stale), { recursive: true, force: true });
const result = spawnSync(process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), "typegen"], { cwd: root, stdio: "inherit" });
process.exitCode = result.status ?? 1;
