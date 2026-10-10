/** Start the configured native stack without downloading models or changing application data. */
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { access, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import nextEnvironment from "@next/env";

const root = fileURLToPath(new URL("..", import.meta.url));
const nativeEnvironment = { ...process.env };
const python = resolve(root, "myenev/bin/python");
const output = resolve(root, ".local/runtime");
const development = process.argv.includes("--dev");
const children = [];
let stopping = false;

function backendCommand(code) {
  try {
    return execFileSync(python, ["-c", code], {
      cwd: root, env: nativeEnvironment, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 15000,
    });
  } catch {
    throw new Error("Backend prerequisites failed. Check myenev, backend/.env and the database privately.");
  }
}

function loopbackOrigin(value, label) {
  let url;
  try { url = new URL(value); } catch { throw new Error(`${label} requires a configured HTTP loopback origin.`); }
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error(`${label} requires an HTTP loopback origin without credentials or a path.`);
  }
  return url;
}

function start(name, executable, args, environment = nativeEnvironment) {
  const log = createWriteStream(resolve(output, `${name}.log`), { flags: "a", mode: 0o600 });
  const child = spawn(executable, args, { cwd: root, env: environment, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  children.push(child);
  child.stdout.pipe(log, { end: false });
  child.stderr.pipe(log, { end: false });
  child.on("close", () => log.end());
  child.on("error", () => {
    console.error(`Could not start ${name}. Check .local/runtime/${name}.log.`);
    void stop(1);
  });
  child.on("exit", (code) => {
    if (!stopping) {
      console.error(`${name} stopped (${code ?? "signal"}). Check .local/runtime/${name}.log.`);
      void stop(1);
    }
  });
  console.log(`Starting ${name}; log: .local/runtime/${name}.log`);
}

async function getJson(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2000), redirect: "error" });
    return response.ok ? await response.json() : null;
  } catch { return null; }
}

async function waitFor(check, label, seconds = 30) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline && !stopping) {
    if (await check()) return;
    await new Promise((done) => setTimeout(done, 500));
  }
  throw new Error(`${label} did not become ready. Check .local/runtime/ logs and configuration.`);
}

async function stop(code) {
  if (stopping) return;
  stopping = true;
  const running = children.filter((child) => child.pid && child.exitCode === null && child.signalCode === null);
  for (const child of running) {
    try { process.kill(-child.pid, "SIGTERM"); } catch { /* Already exited. */ }
  }
  await Promise.race([
    Promise.all(running.map((child) => new Promise((done) => child.once("exit", done)))),
    new Promise((done) => setTimeout(done, 5000)),
  ]);
  for (const child of running) {
    if (child.exitCode === null && child.signalCode === null) {
      try { process.kill(-child.pid, "SIGKILL"); } catch { /* Already exited. */ }
    }
  }
  process.exit(code);
}
process.on("SIGINT", () => { void stop(0); });
process.on("SIGTERM", () => { void stop(0); });

try {
  await access(python);
  await mkdir(output, { recursive: true, mode: 0o700 });
  const configuration = JSON.parse(backendCommand(`
import hashlib, json
from pathlib import Path
from backend.config import get_settings
s = get_settings()
u = s.sqlalchemy_url()
print(json.dumps({
    "gateway_hash": hashlib.sha256(s.fixflow_api_token.get_secret_value().encode()).hexdigest() if s.fixflow_api_token else None,
    "public_origin": s.connectors.public_url,
    "answer_context": s.generation_context_tokens,
    "answer_timeout": s.generation_timeout_seconds,
    "local_database": u.query.get("host") == str(Path(".local/postgres").resolve()),
    "models": [
        {"origin": s.ollama_url, "model": s.embedding_model, "digest": s.embedding_model_digest, "answer": False},
        {"origin": s.generation_ollama_url, "model": s.generation_model, "digest": s.generation_model_digest, "answer": True},
    ],
}))
`));
  process.env.NODE_ENV = development ? "development" : "production";
  nextEnvironment.loadEnvConfig(root, development);
  const token = process.env.FIXFLOW_API_TOKEN;
  if (!token || createHash("sha256").update(token).digest("hex") !== configuration.gateway_hash) {
    throw new Error("Frontend and backend FIXFLOW_API_TOKEN must match. Review environment files privately.");
  }
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY || !process.env.CLERK_SECRET_KEY) {
    throw new Error("Configure both Clerk keys in the native frontend environment.");
  }
  const frontend = loopbackOrigin(configuration.public_origin, "CONNECTORS__PUBLIC_URL");
  const backend = loopbackOrigin(process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL, "INTERNAL_API_URL");
  if (process.env.CONNECTORS__PUBLIC_URL && new URL(process.env.CONNECTORS__PUBLIC_URL).origin !== frontend.origin) {
    throw new Error("Frontend and backend CONNECTORS__PUBLIC_URL must match.");
  }
  if (!development) {
    try { await access(resolve(root, ".next/standalone/server.js")); }
    catch { throw new Error("Production build is missing. Run npm run build, or use npm run dev:local."); }
  }
  if (configuration.local_database) {
    execFileSync("/bin/bash", ["scripts/local_postgres.sh", "start"], { cwd: root, stdio: "pipe", timeout: 15000 });
  }
  const database = JSON.parse(backendCommand(`
import asyncio, json
from backend.services.readiness import database_readiness
from backend.db.session import close_database
async def check():
    try:
        print(json.dumps(await database_readiness()))
    finally:
        await close_database()
asyncio.run(check())
`));
  if (database.status !== "ok") throw new Error("Database/schema is unavailable. Review configuration and required migrations.");
  const models = configuration.models.filter((model) => model.origin);
  const origins = new Set();
  for (const model of models) {
    const origin = loopbackOrigin(model.origin, "Ollama");
    if (!origins.has(origin.origin) && !await getJson(`${origin.origin}/api/tags`)) {
      const separate = models.length === 2 && models[0].origin !== models[1].origin;
      const answers = models.some((item) => item.answer && item.origin === model.origin);
      start(answers ? "ollama-answers" : "ollama-embeddings", "ollama", ["serve"], {
        ...nativeEnvironment, OLLAMA_HOST: origin.host, OLLAMA_NUM_PARALLEL: "1", OLLAMA_MAX_LOADED_MODELS: "1",
        OLLAMA_FLASH_ATTENTION: "1", OLLAMA_KV_CACHE_TYPE: model.answer && separate ? "q8_0" : "f16",
        // The installed Qwen instruction model's GGUF template advertises thinking;
        // force its non-thinking Ollama template rather than automatic GGUF selection.
        ...(answers ? { OLLAMA_GO_TEMPLATE: "1" } : {}),
        ...(!model.answer && separate ? { CUDA_VISIBLE_DEVICES: "-1", OLLAMA_VULKAN: "0" } : {}),
      });
      await waitFor(async () => Boolean(await getJson(`${origin.origin}/api/tags`)), "Ollama");
    }
    origins.add(origin.origin);
    const inventory = await getJson(`${origin.origin}/api/tags`);
    if (!inventory?.models?.some((item) => item.name === model.model && item.digest === model.digest)) {
      throw new Error("A configured model pin is missing. Install and verify the documented model before starting.");
    }
    if (model.answer) {
      console.log("Warming the pinned answer model and checking structured output…");
      const response = await fetch(`${origin.origin}/api/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" }, redirect: "error",
        signal: AbortSignal.timeout(configuration.answer_timeout * 1000),
        body: JSON.stringify({
          model: model.model, stream: false, think: false, keep_alive: "5m",
          messages: [{ role: "user", content: 'Return exactly the JSON object {"ready": true}.' }],
          format: { type: "object", properties: { ready: { type: "boolean", const: true } },
            required: ["ready"], additionalProperties: false },
          options: { temperature: 0, num_predict: 64, num_ctx: configuration.answer_context, num_batch: 128 },
        }),
      });
      const body = await response.text();
      let completed = false;
      try {
        if (body.length <= 64 * 1024) {
          const payload = JSON.parse(body);
          const value = JSON.parse(payload.message?.content ?? "");
          completed = response.ok && payload.model === model.model && payload.done === true
            && payload.done_reason === "stop" && payload.message.role === "assistant"
            && !payload.message.tool_calls?.length && value?.ready === true && Object.keys(value).length === 1;
        }
      } catch { /* Invalid model output is a startup failure, never an accepted answer. */ }
      const current = await getJson(`${origin.origin}/api/tags`);
      if (!completed || !current?.models?.some((item) => item.name === model.model && item.digest === model.digest)) {
        throw new Error("The pinned answer runtime failed its structured-output check. Review .local/runtime/ logs.");
      }
    }
  }
  const existingBackend = await getJson(`${backend.origin}/health`);
  if (existingBackend?.service !== "fixflow-api" || existingBackend.status !== "ok") {
    start("backend", python, ["-m", "uvicorn", "backend.main:app", "--host", backend.hostname.replace(/^\[|\]$/g, ""),
      "--port", backend.port || "80"]);
    await waitFor(async () => (await getJson(`${backend.origin}/health`))?.status === "ok", "Backend");
  }
  const authenticated = await fetch(`${backend.origin}/api/readiness`, {
    headers: { Authorization: `Bearer ${token}`, "X-FixFlow-User-Id": "user_launch_probe" },
    signal: AbortSignal.timeout(10000), redirect: "error",
  });
  if (!authenticated.ok) throw new Error("The running backend rejected the configured gateway. Restart it with the current native environment.");
  const readiness = await authenticated.json();
  if (models.some((model) => model.answer) && readiness.answer_service !== "ready") {
    throw new Error("The running backend answer service is unavailable. Restart it with the current native environment.");
  }
  start("frontend", process.execPath, development
    ? [resolve(root, "node_modules/next/dist/bin/next"), "dev", "--hostname", frontend.hostname, "--port", frontend.port || "80"]
    : [resolve(root, "scripts/start-production.mjs"), "--hostname", frontend.hostname, "--port", frontend.port || "80"],
  { ...process.env });
  await waitFor(async () => {
    try { return (await fetch(`${frontend.origin}/sign-up`, { signal: AbortSignal.timeout(2000), redirect: "manual" })).status === 200; }
    catch { return false; }
  }, "Frontend", 60);
  console.log(`FixFlow is ready at ${frontend.origin}. Ctrl+C stops processes started by this command; PostgreSQL stays running.`);
} catch (error) {
  console.error(error instanceof Error ? error.message : "Local startup failed.");
  await stop(1);
}
