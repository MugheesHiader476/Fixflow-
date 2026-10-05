/**
 * Browser proof using real application components.
 * Install Playwright in /tmp/fixflow-browser-tools; start the Vite harness and
 * an isolated FastAPI test instance backed by a disposable PostgreSQL database.
 * Clerk and Next routing are mocked ONLY by the test harness. Live mode uses
 * the real API, ingestion worker and PostgreSQL database.
 */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to your installed Playwright package directory.");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const base = "http://127.0.0.1:4173";
const api = `${base}/api/backend`;
const output = resolve(".local/browser-evidence");
await mkdir(output, { recursive: true });
const report = { startedAt: new Date().toISOString(), harness: "Actual React components; test-only Clerk, Image and Next navigation adapters", checks: [], requests: [], browserErrors: [] };
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", (error) => report.browserErrors.push(error.message));
async function check(name, mode, run) {
  if (process.env.FIXFLOW_CORE_ONLY === "1" && (mode === "mock" || name.startsWith("Mobile"))) return;
  const started = Date.now();
  try { await run(); report.checks.push({ name, mode, status: "passed", ms: Date.now() - started }); console.log(`PASS [${mode}] ${name}`); }
  catch (error) { report.checks.push({ name, mode, status: "failed", message: error.message }); throw error; }
}
async function noOverflow() { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Unexpected horizontal overflow"); }
async function capture(name, fullPage = true) {
  if (process.env.FIXFLOW_FAILURE_ARTIFACTS_ONLY === "1" && name !== "failure.png") return;
  await page.screenshot({ path: resolve(output, name), fullPage, animations: "disabled" });
}
const health = { status: "ok", service: "fixflow-api", api: "ok", revision: "0002", expected_revision: "0002", pending_sources: 0, failed_sources: 0, database: "connected", pgvector: "available", schema: "ready", sources: 0, documents: 0, chunks: 0, embedded_chunks: 0, embedding_configured: false, ai_generation: "not_configured" };
try {
  await context.route(`${api}/**`, (route) => route.fulfill({ json: route.request().url().endsWith("/health") ? health : [] }));
  await check("Desktop hero, local image loading and working start link", "mock", async () => {
    await page.goto(base, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: /Less searching.*More building/ })).toBeVisible();
    assert.ok(await page.locator(".ff-hero img").evaluate((img) => img.complete && img.naturalWidth > 0));
    assert.ok(await page.locator(".ff-guide-image img").evaluate((img) => img.complete && img.naturalWidth > 0));
    await noOverflow();
    await capture("workspace-desktop.png");
    await page.getByRole("link", { name: "Start a session", exact: true }).click();
    await expect(page.getByRole("heading", { name: "What are you working on?" })).toBeInViewport();
  });
  await check("Empty input validation prevents a search", "mock", async () => {
    await page.getByRole("button", { name: "Search documentation", exact: true }).click();
    await expect(page.getByText("Add an error message, code, or context first")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Error message" })).toBeFocused();
  });
  await check("Native modal menu isolates page and Escape restores focus", "mock", async () => {
    const opener = page.getByRole("button", { name: "Open menu" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "Workspace menu" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("link", { name: "Settings", exact: true }).focus();
    await page.keyboard.press("Tab");
    // Native dialogs may briefly focus browser chrome at the end of their tab order.
    if (await page.evaluate(() => document.activeElement === document.body)) await page.keyboard.press("Tab");
    assert.ok(await page.evaluate(() => Boolean(document.activeElement?.closest("dialog"))), "Focus reached a background control");
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(opener).toBeFocused();
  });
  await check("Dark theme is usable and persists across a reload", "mock", async () => {
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await capture("workspace-dark.png");
    await page.getByRole("button", { name: "Toggle theme" }).click();
  });
  await check("Mobile layouts at 390px and 320px and reachable navigation", "mock", async () => {
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(base, { waitUntil: "networkidle" });
      await noOverflow();
      if (width === 390) await capture("workspace-mobile.png");
      await page.getByRole("button", { name: "Open menu" }).click();
      const menu = page.getByRole("dialog", { name: "Workspace menu" });
      await expect(menu).toBeVisible();
      await menu.getByRole("link", { name: "Knowledge Sources" }).click();
      await expect(page.getByRole("heading", { name: "Good context starts here." })).toBeVisible();
      await noOverflow();
    }
    await capture("sources-mobile.png");
    await page.setViewportSize({ width: 1440, height: 1000 });
  });
  await check("History and saved empty states, settings and disabled remote ingestion", "mock", async () => {
    await page.goto(`${base}/history`);
    await expect(page.getByText("No debug sessions yet.")).toBeVisible();
    await page.goto(`${base}/saved`);
    await expect(page.getByText("No saved solutions yet.")).toBeVisible();
    await page.goto(`${base}/sources`);
    await expect(page.getByRole("tab", { name: /Documentation URL/ })).toBeDisabled();
    await page.goto(`${base}/settings`);
    await expect(page.getByText("Not connected", { exact: true })).toBeVisible();
    await capture("settings-desktop.png");
  });
  await check("Offline errors and working history retry", "mock", async () => {
    await context.unroute(`${api}/**`);
    await context.route(`${api}/**`, (route) => route.fulfill({ status: 503, json: { error: { message: "Verification: backend unavailable" } } }));
    await page.goto(`${base}/history`);
    await expect(page.getByText("Offline", { exact: true })).toBeVisible();
    await expect(page.getByText("Verification: backend unavailable", { exact: true })).toBeVisible();
    await capture("offline-state.png");
    await context.unroute(`${api}/**`);
    await context.route(`${api}/**`, (route) => route.fulfill({ json: [] }));
    await page.getByRole("button", { name: "Refresh history" }).click();
    await expect(page.getByText("No debug sessions yet.")).toBeVisible();
  });
  await context.unroute(`${api}/**`);
  page.on("response", (response) => {
    if (response.url().startsWith(api)) report.requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() });
  });
  let sessionId;
  const marker = `verification${Date.now()}`;
  const runbookTitle = `FixFlow verification runbook ${marker}`;
  await check("Real PostgreSQL API health", "live API", async () => {
    const response = await context.request.get(`${api}/health`);
    assert.equal(response.status(), 200);
    const value = await response.json();
    assert.equal(value.database, "connected");
    assert.equal(value.schema, "ready");
    report.liveHealth = value;
  });
  await check("Paste document, accept 202, poll completed real ingestion", "live API", async () => {
    await page.goto(`${base}/sources`);
    await page.getByLabel("Document title").fill(runbookTitle);
    await page.getByLabel("Documentation content").fill(`# Async event loop recovery ${marker}\n\nRuntimeError: no running event loop occurs when asyncio.create_task is called without an active event loop. Use asyncio.run(main()) at the entry point. Within main, await coroutines or schedule them with asyncio.create_task. ${marker} documents this reference procedure.`);
    const accepted = page.waitForResponse((r) => r.url() === `${api}/api/documents` && r.request().method() === "POST");
    await page.getByRole("button", { name: "Add to knowledge base" }).click();
    const response = await accepted;
    assert.equal(response.status(), 202);
    const source = await response.json();
    report.sourceId = source.id;
    await expect(page.getByText("Ready for embedding", { exact: true }).first()).toBeVisible({ timeout: 20000 });
    const persisted = await (await context.request.get(`${api}/api/sources/${source.id}`)).json();
    assert.equal(persisted.status, "ready_for_embedding");
    assert.ok(persisted.chunk_count > 0);
    report.ingestion = { status: persisted.status, documents: persisted.document_count, chunks: persisted.chunk_count };
    await capture("sources-desktop.png");
  });
  await check("File upload preserves file and reaches retrieval-ready state", "live API", async () => {
    await page.getByRole("tab", { name: /^Upload a file/ }).click();
    await page.getByLabel("Upload document").setInputFiles({ name: `verification-notes-${marker}.md`, mimeType: "text/markdown", buffer: Buffer.from(`# Verification notes\n\n${marker}: Keep the event loop active before scheduling coroutines. These are synthetic test notes.`) });
    const accepted = page.waitForResponse((r) => r.url() === `${api}/api/documents` && r.request().method() === "POST");
    await page.getByRole("button", { name: "Add to knowledge base" }).click();
    const source = await (await accepted).json();
    await expect.poll(async () => (await (await context.request.get(`${api}/api/sources/${source.id}`)).json()).status, { timeout: 20000 }).toBe("ready_for_embedding");
  });
  await check("Keyboard submission sends inputs and displays real retrieved evidence", "live API", async () => {
    await page.goto(base);
    await page.getByRole("link", { name: "Start a session", exact: true }).click();
    await page.getByRole("textbox", { name: "Error message" }).fill(`RuntimeError: no running event loop ${marker}`);
    const result = page.waitForResponse((r) => r.url() === `${api}/api/debug` && r.request().method() === "POST");
    await page.getByRole("textbox", { name: "Error message" }).press("Control+Enter");
    const response = await result;
    assert.equal(response.status(), 200);
    const diagnosis = await response.json();
    sessionId = diagnosis.sessionId;
    assert.equal(diagnosis.generation, "disabled");
    assert.equal(diagnosis.confidence, null);
    assert.ok(diagnosis.sources.some((source) => source.title.includes(marker)));
    assert.ok(diagnosis.request.error.includes(marker));
    await expect(page.getByRole("region", { name: "Evidence and sources" })).toBeVisible();
    await expect(page.getByText("Documentation search is available. AI diagnosis and suggested code changes are not connected yet.")).toBeVisible();
    await expect(page.getByText("keyword match", { exact: true }).first()).toBeVisible();
    report.retrieval = { sessionId, generation: diagnosis.generation, confidence: diagnosis.confidence, sources: diagnosis.sources.length };
    await capture("retrieval-desktop.png");
  });
  await check("Save writes to PostgreSQL and saved detail is readable", "live API", async () => {
    const saved = page.waitForResponse((r) => r.url() === `${api}/api/saved` && r.request().method() === "POST");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), 200);
    report.savedId = (await response.json()).id;
    await expect(page.getByRole("button", { name: "Saved", exact: true })).toBeDisabled();
    await page.goto(`${base}/saved`);
    await expect(page.getByRole("heading", { name: `RuntimeError: no running event loop ${marker}` })).toBeVisible();
    await page.locator("article").filter({ hasText: marker }).getByText("View saved details").click();
    await expect(page.getByText("Related documentation found; a root cause has not been determined.", { exact: true }).first()).toBeVisible();
    await capture("saved-desktop.png");
  });
  await check("History reopens persisted inputs and follow-up survives reload", "live API", async () => {
    await page.goto(`${base}/history`);
    await page.getByRole("main").locator(`a[href='/?session=${sessionId}']`).click();
    await expect(page.getByRole("textbox", { name: "Error message" })).toHaveValue(`RuntimeError: no running event loop ${marker}`);
    const question = `What does the runbook say about asyncio ${marker}?`;
    await page.getByRole("textbox", { name: "Follow-up question" }).fill(question);
    await page.getByRole("button", { name: "Send follow-up" }).click();
    await expect(page.getByText(/Matching documentation \(AI generation is not connected\)/)).toBeVisible();
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.getByText(question, { exact: true })).toBeVisible();
    await expect(page.getByText(/Matching documentation \(AI generation is not connected\)/)).toBeVisible();
    const messages = await (await context.request.get(`${api}/api/sessions/${sessionId}/messages`)).json();
    assert.ok(messages.some((message) => message.role === "user" && message.text === question));
    assert.ok(messages.some((message) => message.role === "fixflow" && message.sources.length > 0));
    report.persistedMessageCount = messages.length;
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow();
    await capture("retrieval-mobile.png");
  });
  await check("Mobile context panel opens and closes", "live API", async () => {
    await page.getByRole("button", { name: "Toggle context panel" }).click();
    await expect(page.getByRole("button", { name: "Close panel" })).toBeVisible();
    await page.getByRole("button", { name: "Context", exact: true }).last().click();
    await expect(page.getByText("Not assessed", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Close panel" }).click();
    await expect(page.getByRole("button", { name: "Close panel" })).not.toBeVisible();
  });
  await check("No browser runtime errors", "all", async () => assert.deepEqual(report.browserErrors, []));
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  console.error(error);
  await capture("failure.png");
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await writeFile(resolve(output, "browser-report.json"), JSON.stringify(report, null, 2) + "\n");
  await browser.close();
}
