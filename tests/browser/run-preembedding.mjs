/** Real Next → FastAPI → PostgreSQL E2E. Only external Clerk identity is simulated. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { cp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(".");
const output = resolve(".local/preembedding");
const staging = resolve(output, "app");
const python = resolve("myenev/bin/python");
const database = process.env.TEST_DATABASE_URL;
assert.ok(database, "Set TEST_DATABASE_URL to a separate disposable browser database ending _test");
assert.ok(new URL(database).pathname.endsWith("_test"), "Refusing a non-test database");
const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to the installed Playwright package directory");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const ui = "http://127.0.0.1:3012", api = `${ui}/api/backend/api`;
const environment = {
  ...process.env, DATABASE_URL: database, FIXFLOW_API_TOKEN: "test-only-preembedding-gateway-token-32-characters",
  FIXFLOW_DATA_DIR: resolve(output, "uploads"), OLLAMA_URL: "", EMBEDDING_MODEL_DIGEST: "", EMBEDDING_PROFILE: "plain-v1", EMBEDDING_API_URL: "", EMBEDDING_MODEL: "", EMBEDDING_DIM: "",
  RETRIEVAL_MODE: "keyword", EMBEDDING_AUTO_PROCESS: "false",
  CONNECTORS__PUBLIC_URL: ui, FRONTEND_ORIGINS: ui, PIPELINE__OCR_ENABLED: "false", PYTHONPATH: root,
};
await mkdir(output, { recursive: true });
const commandLogs = [];
function command(executable, args, options = {}) {
  const child = spawn(executable, args, { cwd: root, env: environment, ...options });
  let stdout = "", stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  child.result = once(child, "exit").then(([code]) => ({ code, stdout, stderr }));
  commandLogs.push({ child, args });
  return child;
}
async function run(executable, args) {
  const result = await command(executable, args).result;
  assert.equal(result.code, 0, `${args.join(" ")}\n${result.stderr}\n${result.stdout}`);
  return result.stdout;
}
async function freePort(port) {
  const server = createServer();
  await new Promise((success, failure) => { server.once("error", failure); server.listen(port, "127.0.0.1", success); });
  await new Promise((success) => server.close(success));
}
await freePort(3012); await freePort(8012);
await run(python, ["-m", "alembic", "upgrade", "head"]);
await run(python, ["tests/browser/preembedding.py", "reset", "database"]);
await run(python, ["tests/browser/preembedding.py", "fixtures", resolve(output, "fixtures")]);
await mkdir(staging, { recursive: true });
// A disposable Next run must not reuse partially written manifests from an interrupted run.
await rm(resolve(staging, ".next"), { recursive: true, force: true });
// Copy application/config sources only, never environment files, user uploads or secrets.
for (const name of ["src", "public", "package.json", "tsconfig.json", "postcss.config.mjs", "next.config.ts"]) {
  await cp(resolve(root, name), resolve(staging, name), { recursive: true, force: true });
}
try { await symlink(resolve(root, "node_modules"), resolve(staging, "node_modules"), "dir"); }
catch (error) { if (error.code !== "EEXIST") throw error; }
// Keep the actual Next configuration and add exact external-Clerk aliases to this disposable copy.
const configPath = resolve(staging, "next.config.ts");
const config = await readFile(configPath, "utf8");
await writeFile(configPath, config.replace("export default nextConfig;", `
nextConfig.webpack = (config) => {
  config.resolve.alias["@clerk/nextjs/server$"] = ${JSON.stringify(resolve(root, "tests/browser/clerk-server.mjs"))};
  config.resolve.alias["@clerk/nextjs$"] = ${JSON.stringify(resolve(root, "tests/browser/clerk-client.tsx"))};
  return config;
};
export default nextConfig;`));
let backend, frontend, browser;
const report = { mode: "Real Next/FastAPI/PostgreSQL/worker/parser/chunker/prepared_source; external Clerk only simulated", checks: [], matrix: [], runtimeErrors: [], consoleErrors: [], requests: [], limitations: ["Live Clerk authentication/provider consent unverified", "Native OCR unavailable: deterministic adapter-output tests run separately"] };
async function startBackend() {
  backend = command(python, ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8012"]);
  await expect.poll(async () => { try { return (await fetch("http://127.0.0.1:8012/health")).status; } catch { return 0; } }, { timeout: 30000 }).toBe(200);
}
async function stopBackend() {
  if (backend && backend.exitCode === null) { backend.kill("SIGTERM"); await backend.result; }
}
function sourceCard(page, name) { return page.getByText(name, { exact: true }).locator("xpath=../../.."); }
async function inspect(id, fixture, owner = "user_preembedding_a") {
  const args = ["tests/browser/preembedding.py", "inspect", id, "--owner", owner];
  if (fixture) args.push("--fixture", fixture);
  return JSON.parse(await run(python, args));
}
async function check(name, action, page) {
  const started = performance.now();
  try { await action(); report.checks.push({ name, result: "PASS", milliseconds: performance.now() - started }); console.log("PASS " + name); }
  catch (error) {
    report.checks.push({ name, result: "FAIL", error: error.message });
    if (page) await page.screenshot({ path: resolve(output, `failure-${report.checks.length}.png`), fullPage: true }).catch(() => {});
    throw error;
  }
}
async function terminal(request, id, expected) {
  const statuses = [];
  let item;
  await expect.poll(async () => {
    const response = await request.get(`${api}/sources/${id}`);
    assert.equal(response.status(), 200); item = await response.json(); statuses.push(item.status);
    return item.status;
  }, { timeout: 120000, intervals: [100, 250, 500] }).toBe(expected);
  return { item, statuses: [...new Set(statuses)] };
}
async function upload(page, fixture) {
  await page.getByRole("tab", { name: /^Upload a file/ }).click();
  await page.getByLabel("Upload document").setInputFiles(fixture.path);
  if (fixture.acceptance === 415) {
    await expect(page.getByRole("button", { name: "Add to knowledge base" })).toBeDisabled();
    await expect(page.getByText("Choose a supported document type.", { exact: true })).toBeVisible();
    const started = performance.now();
    const response = await page.context().request.post(`${api}/documents`, {
      multipart: { file: { name: fixture.name, mimeType: "application/octet-stream", buffer: await readFile(fixture.path) } },
    });
    assert.equal(response.status(), 415);
    return { response, started, acceptanceMs: performance.now() - started, rejectedByUI: true };
  }
  const accepted = page.waitForResponse((response) => response.url() === `${api}/documents` && response.request().method() === "POST");
  const started = performance.now();
  await page.getByRole("button", { name: "Add to knowledge base" }).click();
  const response = await accepted;
  const acceptanceMs = performance.now() - started;
  assert.equal(response.status(), fixture.acceptance);
  if (fixture.acceptance !== 202) return { acceptanceMs, response, started };
  const source = await response.json();
  await expect(sourceCard(page, source.name)).toBeVisible();
  assert.ok(["uploaded", "processing", fixture.expected].includes(source.status));
  return { source, acceptanceMs, response, started };
}
try {
  await startBackend();
  frontend = command(process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), "dev", staging, "--webpack", "--hostname", "127.0.0.1", "--port", "3012"], { cwd: staging, env: { ...environment, INTERNAL_API_URL: "http://127.0.0.1:8012", NEXT_PUBLIC_API_URL: "http://127.0.0.1:8012", NEXT_TELEMETRY_DISABLED: "1" } });
  await expect.poll(async () => { try { return (await fetch(`${ui}/sign-up`)).status; } catch { return 0; } }, { timeout: 90000 }).toBe(200);
  browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  await check("Real Next signed-out gateway/page and forged identity rejected", async () => {
    assert.equal((await context.request.get(`${api}/sources`, { headers: { "X-FixFlow-User-Id": "user_preembedding_a" } })).status(), 401);
    const protectedPage = await context.request.get(`${ui}/sources`, { maxRedirects: 0 });
    assert.equal(protectedPage.status(), 307); assert.equal(new URL(protectedPage.headers().location, ui).pathname, "/sign-up");
    assert.equal((await fetch("http://127.0.0.1:8012/api/sources")).status, 401);
  });
  await context.addCookies([{ name: "fixflow-test-identity", value: "user_preembedding_a", url: ui }]);
  const page = await context.newPage();
  page.on("pageerror", (error) => report.runtimeErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") report.consoleErrors.push(message.text()); });
  page.on("response", (response) => { if (response.url().includes("/api/backend/")) report.requests.push({ path: new URL(response.url()).pathname, method: response.request().method(), status: response.status() }); });
  await context.tracing.start({ screenshots: true, snapshots: true });
  await page.goto(`${ui}/sources`);
  await check("Authenticated actual Next page loads without hydration errors", async () => {
    await expect(page.getByRole("heading", { name: "Add documentation" })).toBeVisible();
  }, page);
  await check("Unbroken long filename remains usable at 390px and 320px", async () => {
    await page.getByRole("tab", { name: /^Upload a file/ }).click();
    await page.getByLabel("Upload document").setInputFiles({ name: "long" + "x".repeat(235) + ".txt", mimeType: "text/plain", buffer: Buffer.from("safe evidence") });
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Long filename overflow at ${width}px`);
      await page.screenshot({ path: resolve(output, `selected-file-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "Remove file" }).click();
  }, page);
  const manifest = JSON.parse(await readFile(resolve(output, "fixtures/manifest.json"), "utf8"));
  const filter = process.env.FIXFLOW_E2E_ONLY;
  const fixtures = filter ? manifest.filter((fixture) => fixture.name.includes(filter)) : manifest;
  for (const fixture of fixtures) {
    await check(`Browser → prepared: ${fixture.name}`, async () => {
      const result = await upload(page, fixture);
      if (fixture.acceptance !== 202) {
        if (!result.rejectedByUI) await expect(page.getByRole("button", { name: "Add to knowledge base" })).toBeEnabled();
        report.matrix.push({ ...fixture, result: "PASS", acceptance_ms: result.acceptanceMs }); return;
      }
      const finished = await terminal(context.request, result.source.id, fixture.expected);
      const terminalMs = performance.now() - result.started;
      const card = sourceCard(page, result.source.name);
      await expect(card.getByText(fixture.expected === "ready_for_embedding" ? "Ready for embedding" : "Failed", { exact: true })).toBeVisible({ timeout: 10000 });
      const persisted = await inspect(result.source.id, fixture.path);
      assert.equal(persisted.source_hash, fixture.sha256, "Upload changed the original bytes");
      if (fixture.expected === "ready_for_embedding" && persisted.canonical_text) {
        for (const term of fixture.sanity_terms) assert.ok(persisted.canonical_text.includes(term), `Parser lost ${term} in ${fixture.name}`);
      }
      report.matrix.push({ ...fixture, ...persisted, result: "PASS", acceptance_ms: result.acceptanceMs, terminal_ms: terminalMs, statuses: [result.source.status, ...finished.statuses] });
      if (["normal-short.txt", "hard-sentence.txt", "stress.txt", "invalid.json"].includes(fixture.name)) await page.screenshot({ path: resolve(output, fixture.name + ".png"), fullPage: true });
    }, page);
  }
  if (!filter) {
    await check("Paste submits once by keyboard and persists a Unicode source", async () => {
      await page.getByRole("tab", { name: /^Paste documentation/ }).click();
      await page.getByLabel("Document title").fill("Browser pasted guide");
      await page.getByLabel("Documentation content").fill("# Paste guide\n\nPASTESTART67213 café 日本語 🚀.\n\nPASTEMIDDLE67213 authoritative scope.\n\nPASTEEND67213 final exception.");
      const before = report.requests.filter((r) => r.path.endsWith("/documents")).length;
      const response = page.waitForResponse((r) => r.url() === `${api}/documents` && r.request().method() === "POST");
      await page.getByRole("button", { name: "Add to knowledge base" }).press("Enter");
      const accepted = await response; assert.equal(accepted.status(), 202); const source = await accepted.json();
      await terminal(context.request, source.id, "ready_for_embedding");
      const prepared = await inspect(source.id);
      assert.ok(prepared.canonical_text.includes("café 日本語 🚀"));
      assert.equal(report.requests.filter((r) => r.path.endsWith("/documents")).length - before, 1);
      report.paste = prepared;
    }, page);
    await check("Refresh during processing, interrupted worker and restart retain one source", async () => {
      const name = "long-filename-" + "authoritative-context-".repeat(10) + ".txt";
      const path = resolve(output, "fixtures", name);
      await writeFile(path, "RESTARTBEGIN68213 " + "safe evidence café 日本語 🚀 " .repeat(18000) + " RESTARTEND68213");
      const result = await upload(page, { name, path, acceptance: 202, expected: "ready_for_embedding" });
      await expect.poll(async () => (await (await context.request.get(`${api}/sources/${result.source.id}`)).json()).status, { timeout: 30000, intervals: [50, 100] }).toBe("processing");
      await page.reload();
      await expect(sourceCard(page, result.source.name)).toBeVisible();
      await page.screenshot({ path: resolve(output, "refresh-processing.png"), fullPage: true });
      await stopBackend();
      await startBackend();
      const finished = await terminal(context.request, result.source.id, "ready_for_embedding");
      const prepared = await inspect(result.source.id, path);
      assert.equal(prepared.source_version, 1);
      await expect(sourceCard(page, result.source.name).getByText("Ready for embedding", { exact: true })).toBeVisible({ timeout: 15000 });
      report.restart = { ...prepared, statuses: ["uploaded", "processing", ...finished.statuses] };
    }, page);
    await check("Backend outage surfaces an error; retry submits the retained form once", async () => {
      await page.getByRole("tab", { name: /^Paste documentation/ }).click();
      await page.getByLabel("Document title").fill("Outage retry");
      await page.getByLabel("Documentation content").fill("OUTAGERETRY79153 coherent retained source.");
      await stopBackend();
      const failure = page.waitForResponse((r) => r.url() === `${api}/documents` && r.request().method() === "POST");
      await page.getByRole("button", { name: "Add to knowledge base" }).click();
      assert.equal((await failure).status(), 502);
      await expect(page.getByText("Could not connect to the FixFlow backend.", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Add to knowledge base" })).toBeEnabled();
      await startBackend();
      const retry = page.waitForResponse((r) => r.url() === `${api}/documents` && r.request().method() === "POST");
      await page.getByRole("button", { name: "Add to knowledge base" }).click();
      const accepted = await retry; assert.equal(accepted.status(), 202);
      const source = await accepted.json(); await terminal(context.request, source.id, "ready_for_embedding");
      report.outageRetry = await inspect(source.id);
    }, page);
    await check("Repeated real backend polling failures stop; Refresh resumes after recovery", async () => {
      const path = resolve(output, "fixtures", "bounded-polling.txt");
      await writeFile(path, "BOUNDEDBEGIN78217 " + "preserved Unicode café 日本語 🚀 ".repeat(16000) + " BOUNDEDEND78217");
      const result = await upload(page, { path, acceptance: 202, expected: "ready_for_embedding" });
      await expect.poll(async () => (await (await context.request.get(`${api}/sources/${result.source.id}`)).json()).status, { timeout: 30000, intervals: [50, 100] }).toBe("processing");
      await stopBackend();
      await expect(page.getByText("Could not refresh source status. Use Refresh to retry.", { exact: true })).toBeVisible({ timeout: 20000 });
      const before = report.requests.filter((r) => r.path.endsWith("/sources")).length;
      await page.waitForTimeout(4500);
      assert.equal(report.requests.filter((r) => r.path.endsWith("/sources")).length, before);
      await page.screenshot({ path: resolve(output, "bounded-polling-error.png"), fullPage: true });
      await startBackend();
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await terminal(context.request, result.source.id, "ready_for_embedding");
      await expect(sourceCard(page, result.source.name).getByText("Ready for embedding", { exact: true })).toBeVisible({ timeout: 15000 });
      report.boundedRecovery = await inspect(result.source.id, path);
    }, page);
    await check("Real gateway overwrites forged owner; other identity has no private sources/search", async () => {
      const owned = report.matrix.find((f) => f.name === "sentinels.txt");
      const other = await browser.newContext();
      await other.addCookies([{ name: "fixflow-test-identity", value: "user_preembedding_b", url: ui }]);
      assert.deepEqual(await (await other.request.get(`${api}/sources`)).json(), []);
      assert.equal((await other.request.get(`${api}/sources/${owned.source_id}`)).status(), 404);
      const forged = await other.request.get(`${api}/sources/${owned.source_id}`, { headers: { "X-FixFlow-User-Id": "user_preembedding_a", Authorization: "Bearer forged" } });
      assert.equal(forged.status(), 404);
      const search = await other.request.post(`${api}/debug`, { data: { error: "E2EMIDDLE78413" } });
      assert.equal(search.status(), 200); assert.deepEqual((await search.json()).sources, []);
      await other.close();
    }, page);
    await check("Beginning/middle/end keyword sentinels resolve the persisted source", async () => {
      for (const marker of ["E2ESTART78413", "E2EMIDDLE78413", "E2EEND78413"]) {
        const response = await context.request.post(`${api}/debug`, { data: { error: marker } });
        assert.equal(response.status(), 200); const sources = (await response.json()).sources;
        assert.equal(sources[0].title, "sentinels.txt"); assert.ok(sources[0].excerpt.includes(marker));
      }
    }, page);
    await check("Identical browser upload reuses source and deterministic chunk projection", async () => {
      const fixture = manifest.find((f) => f.name === "hard-sentence.txt");
      const original = report.matrix.find((f) => f.name === fixture.name);
      const repeated = await upload(page, fixture);
      assert.equal(repeated.source.id, original.source_id);
      const unchanged = await inspect(original.source_id, fixture.path);
      assert.equal(unchanged.source_version, original.source_version);
      assert.deepEqual(unchanged.statistics, original.statistics);
      assert.deepEqual(unchanged.chunk_ids, original.chunk_ids);
    }, page);
    await check("Owner-scoped identical content keeps distinct source provenance", async () => {
      const original = report.matrix.find((f) => f.name === "normal-short.txt");
      const other = await browser.newContext();
      await other.addCookies([{ name: "fixflow-test-identity", value: "user_preembedding_b", url: ui }]);
      const fixture = manifest.find((f) => f.name === "normal-short.txt");
      const accepted = await other.request.post(`${api}/documents`, { multipart: { file: { name: fixture.name, mimeType: "text/plain", buffer: await readFile(fixture.path) } } });
      assert.equal(accepted.status(), 202); const source = await accepted.json();
      assert.notEqual(source.id, original.source_id);
      await terminal(other.request, source.id, "ready_for_embedding");
      const prepared = await inspect(source.id, fixture.path, "user_preembedding_b");
      assert.equal(prepared.source_hash, original.source_hash);
      assert.notEqual(prepared.canonical_document_id, original.canonical_document_id);
      assert.equal((await context.request.get(`${api}/sources/${source.id}`)).status(), 404);
      await other.close(); report.otherOwner = prepared;
    }, page);
    await check("Changed source version preserves source ID and replaces validated projection", async () => {
      const original = report.matrix.find((f) => f.name === "normal-short.txt");
      const body = "VERSIONBEGIN91283 changed authorized source. VERSIONEND91283.";
      const accepted = await context.request.post(`${api}/documents`, { multipart: { update_source_id: original.source_id, file: { name: "updated.txt", mimeType: "text/plain", buffer: Buffer.from(body) } } });
      assert.equal(accepted.status(), 202); assert.equal((await accepted.json()).id, original.source_id);
      await terminal(context.request, original.source_id, "ready_for_embedding");
      const prepared = await inspect(original.source_id);
      assert.equal(prepared.source_version, original.source_version + 1);
      assert.notEqual(prepared.source_hash, original.source_hash);
      assert.notDeepEqual(prepared.chunk_ids, original.chunk_ids); report.version = prepared;
    }, page);
    await check("Simultaneous real browser uploads finish without duplicate submissions", async () => {
      const second = await context.newPage();
      second.on("pageerror", (error) => report.runtimeErrors.push(error.message));
      await second.goto(`${ui}/sources`);
      const paths = [resolve(output, "fixtures", "parallel-one.txt"), resolve(output, "fixtures", "parallel-two.txt")];
      await writeFile(paths[0], "PARALLELONE78393 coherent first source.");
      await writeFile(paths[1], "PARALLELTWO78393 coherent second source.");
      const results = await Promise.all(paths.map((path, index) => upload(index ? second : page, { path, acceptance: 202, expected: "ready_for_embedding" })));
      assert.notEqual(results[0].source.id, results[1].source.id);
      await Promise.all(results.map((r) => terminal(context.request, r.source.id, "ready_for_embedding")));
      await Promise.all(results.map((r, index) => inspect(r.source.id, paths[index])));
      report.parallel = results.map((r) => r.source.id);
      await second.close();
    }, page);
    await check("Malformed IDs, SQL-like queries and cross-origin writes fail safely", async () => {
      assert.equal((await context.request.get(`${api}/sources/not-a-uuid`)).status(), 404);
      const query = await context.request.post(`${api}/debug`, { data: { error: "'; SELECT harmless; --" } });
      assert.equal(query.status(), 200);
      const rejected = await context.request.post(`${api}/debug`, { headers: { Origin: "https://untrusted.example" }, data: { error: "E2EMIDDLE78413" } });
      assert.equal(rejected.status(), 403);
    }, page);
    await check("Reload and mobile widths restore terminal sources without overflow", async () => {
      await page.reload();
      await expect(sourceCard(page, "sentinels.txt").getByText("Ready for embedding", { exact: true })).toBeVisible();
      for (const width of [390, 320]) {
        await page.setViewportSize({ width, height: 844 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}px`);
        await page.screenshot({ path: resolve(output, `sources-${width}.png`), fullPage: true });
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
    }, page);
    await check("Terminal sources stop source-status polling", async () => {
      const pending = (await (await context.request.get(`${api}/sources`)).json()).filter((s) => ["uploaded", "processing", "chunked"].includes(s.status));
      assert.deepEqual(pending, []);
      await page.waitForTimeout(2200);
      const before = report.requests.filter((r) => r.path.endsWith("/sources")).length;
      await page.waitForTimeout(2600);
      assert.equal(report.requests.filter((r) => r.path.endsWith("/sources")).length, before);
    }, page);
  }
  await check("PostgreSQL has exact source inventory, valid FKs and no embeddings/orphans", async () => {
    const inventory = JSON.parse(await run(python, ["tests/browser/preembedding.py", "integrity", "database"]));
    const expectedIds = new Set(report.matrix.filter((r) => r.source_id).map((r) => r.source_id));
    for (const extra of [report.paste, report.restart, report.outageRetry, report.otherOwner, report.boundedRecovery]) if (extra) expectedIds.add(extra.source_id);
    for (const id of report.parallel || []) expectedIds.add(id);
    assert.deepEqual(inventory.source_ids, [...expectedIds].sort());
    report.database = inventory;
  }, page);
  assert.deepEqual(report.runtimeErrors, []);
  assert.deepEqual(report.consoleErrors.filter((s) => !s.startsWith("Failed to load resource: the server responded with a status of")), []);
  await context.tracing.stop({ path: resolve(output, "browser-trace.zip") });
} finally {
  await writeFile(resolve(output, "browser-report.json"), JSON.stringify(report, null, 2));
  if (browser) await browser.close();
  if (frontend && frontend.exitCode === null) { frontend.kill("SIGTERM"); await frontend.result; }
  await stopBackend();
  for (let index = 0; index < commandLogs.length; index++) {
    const { child, args } = commandLogs[index];
    // SIGTERM shutdown has a null exitCode and a non-null signalCode; retain server logs too.
    if (child.exitCode !== null || child.signalCode !== null) { const result = await child.result; await writeFile(resolve(output, `command-${index}.log`), args.join(" ") + "\n" + result.stdout + result.stderr); }
  }
}
