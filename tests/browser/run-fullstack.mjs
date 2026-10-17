/** Real isolated Next/FastAPI/PostgreSQL/worker test. Only external identity is simulated. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { cp, mkdir, readFile, symlink, writeFile, open } from "node:fs/promises";
import { resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { once } from "node:events";

const root = resolve(".");
const output = resolve(process.env.FIXFLOW_EVALUATION_DIR || ".local/evaluation");
const app = resolve(output, "app");
const python = process.env.FIXFLOW_TEST_PYTHON || resolve("myenev/bin/python");
const children = new Set();
const env = { ...process.env };
assert.ok(env.TEST_DATABASE_URL, "Explicit disposable TEST_DATABASE_URL is required.");
assert.ok(env.FIXFLOW_BROWSER_TOOLS, "Set FIXFLOW_BROWSER_TOOLS to the installed Playwright directory.");
const origins = { frontend: "http://localhost:3012", backend: "http://127.0.0.1:8012" };
// Clear every connector credential/config value rather than inheriting operator credentials.
for (const key of Object.keys(env)) if (key.startsWith("CONNECTORS__") || key.startsWith("CLERK_") || key.startsWith("NEXT_PUBLIC_")) delete env[key];
Object.assign(env, {
  DATABASE_URL: env.TEST_DATABASE_URL, FIXFLOW_DATA_DIR: resolve(output, "uploads"),
  FIXFLOW_API_TOKEN: "evaluation-private-gateway-" + randomBytes(24).toString("hex"),
  CLERK_SECRET_KEY: "evaluation-private-clerk-" + randomBytes(24).toString("hex"),
  CONNECTORS__PUBLIC_URL: origins.frontend, FRONTEND_ORIGINS: origins.frontend,
  INTERNAL_API_URL: origins.backend, CONNECTORS__EVENTS_ENABLED: "false",
  CONNECTORS__VAULT_KEY: randomBytes(32).toString("base64url") + "=",
  AUDIT_PROVIDER_TOKEN: "evaluation-private-provider-" + randomBytes(24).toString("hex"),
});
for (const name of ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_APP_ID", "GITHUB_PRIVATE_KEY", "GITHUB_WEBHOOK_SECRET", "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_SIGNING_SECRET"]) env["CONNECTORS__" + name] = "";
env.CONNECTORS = JSON.stringify({ public_url: origins.frontend, vault_key: env.CONNECTORS__VAULT_KEY, events_enabled: false });
env.FIXFLOW_EVALUATION_DIR = output;
env.FIXFLOW_TEST_FRONTEND = origins.frontend;
env.FIXFLOW_TEST_BACKEND = origins.backend;
env.FIXFLOW_TEST_PYTHON = python;

async function run(name, executable, args, cwd = root) {
  const log = await open(resolve(output, name + ".log"), "w", 0o600);
  const child = spawn(executable, args, { cwd, env, stdio: ["ignore", log.fd, log.fd] });
  children.add(child);
  try {
    const [code] = await once(child, "exit");
    assert.equal(code, 0, name + " failed; inspect its log");
  } finally { children.delete(child); await log.close(); }
}
async function start(name, executable, args, cwd = root) {
  const log = await open(resolve(output, name + ".log"), "w", 0o600);
  const child = spawn(executable, args, { cwd, env, stdio: ["ignore", log.fd, log.fd] });
  children.add(child);
  child.once("exit", () => { children.delete(child); void log.close(); });
  return child;
}
async function ready(url, child) {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    assert.equal(child.exitCode, null, "Test server exited before readiness");
    try { if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).status === 200) return; } catch { /* Startup retry. */ }
    await new Promise((done) => setTimeout(done, 300));
  }
  throw new Error("Test server did not become ready");
}
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const closed = once(child, "exit");
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
  await closed; clearTimeout(timer);
}
await mkdir(output, { recursive: true });
try {
  await run("test-db-guard", python, ["-c", "import os; from sqlalchemy.engine import make_url; assert make_url(os.environ['TEST_DATABASE_URL']).database.endswith('_test'), 'Disposable test DB required'"]);
  for (const origin of Object.values(origins)) {
    let occupied = false;
    try { await fetch(origin, { signal: AbortSignal.timeout(1000) }); occupied = true; } catch { /* Expected free port. */ }
    assert.equal(occupied, false, "Test port already in use: " + origin);
  }
  await run("browser-migrations", python, ["-m", "alembic", "upgrade", "head"]);
  await run("browser-seed", python, ["-m", "backend.tests.evaluation_support", "reset"]);
  if (env.FIXFLOW_REUSE_EVALUATION_BUILD !== "1") {
    await mkdir(app, { recursive: true });
    for (const path of ["src", "public", "package.json", "package-lock.json", "tsconfig.json"]) await cp(resolve(root, path), resolve(app, path), { recursive: true, force: true });
    await mkdir(resolve(app, "test-identity"), { recursive: true });
    for (const path of ["clerk-server.mjs", "clerk-client.tsx"]) await cp(resolve(root, "tests/browser", path), resolve(app, "test-identity", path));
    try { await symlink(resolve(root, "node_modules"), resolve(app, "node_modules"), "dir"); } catch (error) { if (error.code !== "EEXIST") throw error; }
    const config = (await readFile(resolve(root, "next.config.ts"), "utf8"))
      .replace('output: "standalone",', "")
      .replace('  experimental: {', `  webpack(config) {
      config.resolve.alias["@clerk/nextjs/server$"] = ${JSON.stringify(resolve(app, "test-identity/clerk-server.mjs"))};
      config.resolve.alias["@clerk/nextjs$"] = ${JSON.stringify(resolve(app, "test-identity/clerk-client.tsx"))};
      return config;
    },
    experimental: {`);
    await writeFile(resolve(app, "next.config.ts"), config);
    await run("isolated-next-build", process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), "build", "--webpack"], app);
  }
  await run("isolated-client-secrets", python, ["scripts/audit_client.py", "--build", resolve(app, ".next"), "--output", resolve(output, "isolated-client-secrets.json")]);
  const backend = await start("real-backend", python, ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8012"]);
  await ready(origins.backend + "/health", backend);
  const frontend = await start("real-next", process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), "start", "--hostname", "localhost", "--port", "3012"], app);
  await ready(origins.frontend + "/sign-up", frontend);
  await run("fullstack-playwright", process.execPath, [resolve(root, "tests/browser/verify-fullstack.mjs")]);
  console.log("PASS real application browser evaluation; report: " + resolve(output, "fullstack-results.json"));
} finally {
  await Promise.all([...children].map(stop));
}
