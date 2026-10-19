/** Actual browser → Next gateway → FastAPI → PostgreSQL → parsing worker. */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const execute = promisify(execFile);
const output = resolve(process.env.FIXFLOW_EVALUATION_DIR);
const base = process.env.FIXFLOW_TEST_FRONTEND;
const backend = process.env.FIXFLOW_TEST_BACKEND;
const { chromium } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "test.mjs")).href);
const secrets = ["FIXFLOW_API_TOKEN", "CLERK_SECRET_KEY", "CONNECTORS__VAULT_KEY", "AUDIT_PROVIDER_TOKEN"].map((name) => process.env[name]);
const report = { boundary: "Real Next routing/proxy/gateway, FastAPI, PostgreSQL and worker; external Clerk identities simulated", checks: [], errors: [], responses_scanned: 0, sql: null };
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const responses = [];
const contexts = [];
let page;
async function contextFor(identity) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  contexts.push(context);
  if (identity) await context.addCookies([{ name: "fixflow-test-identity", value: identity, url: base, httpOnly: true, sameSite: "Lax" }]);
  return context;
}
function noSecrets(value) {
  for (const secret of secrets) assert.ok(secret && !value.includes(secret), "A server-only secret reached the browser");
}
async function check(name, test) {
  await test(); report.checks.push(name); console.log("PASS " + name);
}
async function body(response) {
  const text = await response.text(); noSecrets(text); report.responses_scanned++; return text;
}
async function noOverflow() {
  try {
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), { timeout: 3000 }).toBe(true);
  } catch (error) {
    const dimensions = await page.evaluate(() => ({
      viewport: innerWidth, page: document.documentElement.scrollWidth,
      elements: [...document.querySelectorAll("body *")].map((element) => ({
        tag: element.tagName, class: element.className?.toString(),
        right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width,
      })).filter((item) => item.right > innerWidth + 1).sort((a, b) => b.right - a.right).slice(0, 20),
    }));
    await writeFile(resolve(output, "overflow-dimensions.json"), JSON.stringify(dimensions, null, 2));
    throw error;
  }
}
async function verify() {
  await mkdir(output, { recursive: true });
  try {
    const signedOut = await contextFor(null);
    await check("Signed-out browser APIs reject forged identity and workspace requires sign-in", async () => {
      const response = await signedOut.request.get(base + "/api/backend/api/sources", { headers: { "X-FixFlow-User-Id": "user_preembedding_a", Authorization: "Bearer browser-forged-token" } });
      assert.equal(response.status(), 401);
      const entry = await signedOut.request.get(base + "/", { maxRedirects: 0 });
      assert.equal(entry.status(), 307); assert.ok(entry.headers().location.includes("/sign-up"));
      await body(response);
    });
    const owner = await contextFor("user_preembedding_a");
    const other = await contextFor("user_preembedding_b");
    page = await owner.newPage();
    await owner.tracing.start({ screenshots: true, snapshots: true, sources: false });
    page.on("pageerror", (error) => report.errors.push(error.message));
    page.on("response", (response) => {
      const kind = response.request().resourceType();
      if (response.url().startsWith(base) && ["document", "script", "fetch", "xhr"].includes(kind)) responses.push(body(response));
    });
    await check("Security headers deny embedding and disable unsafe browser features", async () => {
      const response = await owner.request.get(base + "/sign-up");
      assert.equal(response.headers()["x-frame-options"], "DENY");
      assert.ok(response.headers()["content-security-policy"].includes("frame-ancestors 'none'"));
      assert.equal(response.headers()["x-content-type-options"], "nosniff");
      assert.equal(response.headers()["permissions-policy"], "camera=(), microphone=(), geolocation=()");
      await writeFile(resolve(output, "headers-after.json"), JSON.stringify(response.headers(), null, 2));
    });
    let sourceId;
    await check("Real composer persists a source and worker reaches validated parsed output", async () => {
      await page.goto(base + "/sources", { waitUntil: "networkidle" });
      await page.getByRole("textbox", { name: "Documentation content" }).fill("# Audit runbook\n\nRestart the worker only after checking the queue.\n\n## Ownership\nThis source belongs to account A.");
      await page.getByRole("textbox", { name: "Document title" }).fill("Audit runbook");
      const accepted = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/documents"));
      await page.getByRole("button", { name: "Add to knowledge base" }).click();
      const response = await accepted; assert.equal(response.status(), 202);
      const source = await response.json(); sourceId = source.id;
      assert.equal(source.retrieval_available, false);
      await page.goto(base + "/sources");
      await expect(page.getByText("Parsed", { exact: true })).toBeVisible({ timeout: 30000 });
      await page.screenshot({ path: resolve(output, "real-parsed-source.png"), fullPage: true });
      const details = await owner.request.get(base + "/api/backend/api/sources/" + sourceId);
      assert.equal(details.headers()["cache-control"], "no-store");
      const data = await details.json();
      assert.equal(data.document_count, 1); assert.equal(data.chunk_count, 0);
      for (const key of ["path", "ingestion_metadata", "owner_id", "credential_reference"]) assert.ok(!(key in data));
      const result = await execute(process.env.FIXFLOW_TEST_PYTHON, ["-m", "backend.tests.evaluation_support", "proof", sourceId], { env: process.env });
      report.sql = JSON.parse(result.stdout);
      assert.ok(report.sql.owner_authorized && report.sql.foreign_excluded && report.sql.canonical_blocks > 0);
      assert.equal(report.sql.engine, "3.0.0"); assert.equal(report.sql.chunks, 0);
      await writeFile(resolve(output, "canonical-sql-proof.json"), JSON.stringify(report.sql, null, 2));
    });
    await check("A second account cannot list, read, update or relabel the first account's source", async () => {
      const list = await other.request.get(base + "/api/backend/api/sources"); assert.deepEqual(await list.json(), []);
      assert.equal((await other.request.get(base + "/api/backend/api/sources/" + sourceId)).status(), 404);
      assert.equal((await other.request.post(base + "/api/backend/api/sources/" + sourceId + "/availability", { data: { active: false } })).status(), 404);
      const forged = await owner.request.get(base + "/api/backend/api/sources", { headers: { "X-FixFlow-User-Id": "user_preembedding_b", Authorization: "Bearer forged" } });
      assert.equal((await forged.json())[0].id, sourceId);
    });
    await check("Backend fails closed for missing/wrong gateway credential", async () => {
      for (const headers of [{}, { Authorization: "Bearer wrong", "X-FixFlow-User-Id": "user_preembedding_a" }]) {
        const result = await owner.request.get(backend + "/api/sources", { headers });
        assert.equal(result.status(), 401); await body(result);
      }
    });
    await check("CSRF, route traversal and unknown gateway destinations are rejected", async () => {
      const cross = await owner.request.post(base + "/api/backend/api/saved", { headers: { Origin: "https://attacker.invalid" }, data: {} });
      assert.equal(cross.status(), 403);
      for (const route of ["api/admin", "api/sources/%2e%2e%2fhealth", "api/sources/not-a-uuid"]) {
        const result = await owner.request.get(base + "/api/backend/" + route);
        assert.equal(result.status(), 404);
      }
    });
    await check("Private files are not public routes and OAuth values are absent from access logs", async () => {
      for (const path of ["/.env.local", "/backend/.env", "/.git/config", "/doc/uploads/private.md"]) {
        const response = await owner.request.get(base + path);
        assert.equal(response.status(), 404); await body(response);
      }
      const callback = await owner.request.get(base + "/api/backend/api/connectors/gmail/callback?state=short&code=AUDIT-CODE-CANARY");
      assert.equal(callback.status(), 422);
      const access = await readFile(resolve(output, "real-backend.log"), "utf8");
      assert.ok(access.includes("/api/connectors/gmail/callback"));
      assert.ok(!access.includes("AUDIT-CODE-CANARY"));
    });
    await check("Malformed and oversized uploads fail safely without crashing the application", async () => {
      for (const [name, mimeType, expected] of [["bad.exe", "text/plain", 415], ["bad.pdf", "application/pdf", 422]]) {
        const result = await owner.request.post(base + "/api/backend/api/documents", { multipart: { file: { name, mimeType, buffer: Buffer.from("invalid payload") } } });
        assert.equal(result.status(), expected); await body(result);
      }
      const tooLarge = await owner.request.post(base + "/api/backend/api/documents", { data: Buffer.alloc(53 * 1024 * 1024, 120), headers: { "Content-Type": "application/octet-stream" }, timeout: 30000 });
      assert.equal(tooLarge.status(), 413);
      assert.equal((await owner.request.get(base + "/api/backend/health")).status(), 200);
    });
    await check("Duplicate upload reuses identity and source access changes remain owner-scoped", async () => {
      const original = "# Audit runbook\n\nRestart the worker only after checking the queue.\n\n## Ownership\nThis source belongs to account A.";
      await page.goto(base + "/sources");
      await page.getByRole("textbox", { name: "Documentation content" }).fill(original);
      const duplicate = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/documents"));
      await page.getByRole("button", { name: "Add to knowledge base" }).click();
      const result = await duplicate;
      assert.equal(result.status(), 202); assert.equal((await result.json()).id, sourceId);
      const inactive = await owner.request.post(base + "/api/backend/api/sources/" + sourceId + "/availability", { data: { active: false } });
      assert.equal((await inactive.json()).access_available, false);
      const active = await owner.request.post(base + "/api/backend/api/sources/" + sourceId + "/availability", { data: { active: true } });
      assert.equal((await active.json()).access_available, true);
    });
    await check("Provider credentials never appear in connector API responses", async () => {
      const response = await owner.request.get(base + "/api/backend/api/connectors");
      const json = await response.json(); noSecrets(JSON.stringify(json));
      assert.equal(json.length, 4);
      const accounts = json.flatMap((item) => item.accounts);
      assert.equal(accounts.length, 1);
      for (const key of ["access_token", "refresh_token", "credential_reference", "ciphertext", "meta"]) assert.ok(!(key in accounts[0]));
      const foreign = await other.request.get(base + "/api/backend/api/connectors");
      assert.equal((await foreign.json()).flatMap((item) => item.accounts).length, 0);
      assert.equal((await other.request.get(base + "/api/backend/api/connectors/" + accounts[0].id + "/status")).status(), 404);
    });
    await check("Stored HTML and unsafe links remain text; long private records stay usable on mobile", async () => {
      const payload = { problem: "AUDIT-" + "Unbroken".repeat(1200), rootCause: "<img src=x onerror=window.auditXss=1><script>window.auditXss=1</script>", fixSummary: "Text remains text.", technology: ["T".repeat(200)], sources: [{ title: "Unsafe source", type: "docs", url: "javascript:window.auditXss=1", excerpt: "<b>Untrusted excerpt</b>" }] };
      const saved = await owner.request.post(base + "/api/backend/api/saved", { data: payload });
      assert.equal(saved.status(), 200);
      assert.deepEqual(await (await other.request.get(base + "/api/backend/api/saved")).json(), []);
      await page.goto(base + "/saved", { waitUntil: "networkidle" });
      await page.getByText("View saved details").click();
      assert.equal(await page.locator("article img, article script").count(), 0);
      assert.equal(await page.locator('a[href^="javascript:"]').count(), 0);
      assert.equal(await page.evaluate(() => window.auditXss), undefined);
      for (const width of [320, 390, 1440]) { await page.setViewportSize({ width, height: 950 }); await noOverflow(); }
      await page.screenshot({ path: resolve(output, "safe-saved-content.png"), fullPage: true });
    });
    await check("Transient gateway failure is visible and Refresh restores real data", async () => {
      await page.route("**/api/backend/api/sources", (route) => route.fulfill({ status: 503, json: { error: { message: "Temporary evaluation outage" } } }));
      await page.goto(base + "/sources");
      await expect(page.getByRole("alert").filter({ hasText: "Could not load knowledge sources." })).toBeVisible();
      await page.unroute("**/api/backend/api/sources");
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await expect(page.getByText("Parsed", { exact: true })).toBeVisible();
    });
    await check("Historical snapshots remain readable and foreign sessions stay private", async () => {
      const seeded = JSON.parse(await readFile(resolve(output, "browser-seed.log"), "utf8"));
      assert.equal((await other.request.get(base + "/api/backend/api/sessions/" + seeded.history_id)).status(), 404);
      await page.goto(base + "/?session=" + seeded.history_id);
      await expect(page.getByText("Historical finding", { exact: true })).toBeVisible();
    });
    await check("Retired model actions and disabled event ingress reject new work", async () => {
      for (const [route, data] of [["ask", { question: "Can you answer?" }], ["debug", { error: "Example" }], ["chat", { session_id: "old", question: "Why?" }]]) {
        assert.equal((await owner.request.post(base + "/api/backend/api/" + route, { data })).status(), 410);
      }
      const event = await signedOut.request.post(base + "/api/connectors/events/github", { data: {} });
      assert.equal(event.status(), 503);
      await body(event);
    });
    await check("Simple chat gateway preserves authentication, strict read-only input and safe model-unavailable errors", async () => {
      const route = base + "/api/backend/api/chat/messages";
      assert.equal((await signedOut.request.post(route, { data: { question: "Hello" } })).status(), 401);
      const forbidden = await owner.request.post(route, { data: { question: "Hello", allow_email_actions: true } });
      assert.equal(forbidden.status(), 422); await body(forbidden);
      const unavailable = await owner.request.post(route, { data: { question: "Hello" } });
      assert.equal(unavailable.status(), 503); await body(unavailable);
      assert.deepEqual(await (await owner.request.get(route)).json(), []);
      await page.goto(base, { waitUntil: "networkidle" });
      await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();
      await expect(page.getByText("Run workflow", { exact: true })).toHaveCount(0);
    });
    await Promise.all(responses); assert.deepEqual(report.errors, []);
    report.checks.push("Browser HTML, JavaScript and API payloads contain none of four private canaries; no uncaught page errors");
  } catch (error) {
    report.failure = error.message;
    if (page) await page.screenshot({ path: resolve(output, "fullstack-failure.png"), fullPage: true });
    throw error;
  } finally {
    for (const context of contexts) {
      try { await context.tracing.stop({ path: resolve(output, "fullstack-" + contexts.indexOf(context) + ".zip") }); } catch { /* Some contexts have no tracing. */ }
    }
    await writeFile(resolve(output, "fullstack-results.json"), JSON.stringify(report, null, 2));
    await browser.close();
  }
}
await verify();
