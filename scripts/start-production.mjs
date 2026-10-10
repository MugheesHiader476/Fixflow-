/** Run the installed Next standalone build with native environment and static assets. */
import { spawn } from "node:child_process";
import { access, cp } from "node:fs/promises";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import nextEnvironment from "@next/env";

process.env.NODE_ENV = "production";
const root = resolve(".");
nextEnvironment.loadEnvConfig(root);
const { values } = parseArgs({ options: {
  hostname: { type: "string", short: "H", default: "localhost" },
  port: { type: "string", short: "p", default: process.env.PORT || "3000" },
} });
if (!/^\d+$/.test(values.port) || Number(values.port) < 1 || Number(values.port) > 65535) {
  throw new Error("Choose a port between 1 and 65535.");
}
const standalone = resolve(root, ".next/standalone");
const server = resolve(standalone, "server.js");
try { await access(server); }
catch { throw new Error("Production build is missing. Run npm run build first."); }
await cp(resolve(root, "public"), resolve(standalone, "public"), { recursive: true, force: true });
await cp(resolve(root, ".next/static"), resolve(standalone, ".next/static"), { recursive: true, force: true });
const child = spawn(process.execPath, [server], {
  cwd: standalone, stdio: "inherit",
  env: { ...process.env, HOSTNAME: values.hostname, PORT: values.port },
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("error", () => { console.error("Could not start the production server."); process.exitCode = 1; });
child.on("exit", (code, signal) => { process.exitCode = code ?? (signal === "SIGTERM" || signal === "SIGINT" ? 0 : 1); });
