/** Actual parsing-workspace components with mocked gateway HTTP/Clerk. No live auth or parsing claim. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to the installed Playwright package directory.");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const output = resolve(".local/browser-evidence");
await mkdir(output, { recursive: true });
const base = "http://127.0.0.1:4173";
const report = { boundary: "Actual React UI; mocked gateway HTTP, Clerk and Next adapters", checks: [], errors: [], requests: [] };
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", (error) => report.errors.push(error.message));
const health = { status: "ok", service: "fixflow-api", api: "ok", database: "connected", pgvector: "available",
  schema: "ready", revision: "0010", expected_revision: "0010", sources: 1, documents: 1, chunks: 0,
  embedded_chunks: 0, pending_sources: 0, failed_sources: 0, embedding_configured: false,
  ai_generation: "removed", answer_service: "removed", retrieval_mode: "removed", processing_mode: "parsing_only",
  workspace_agent: "not_configured", evidence_search: "project_opt_in", evidence_generation: "not_configured",
  evidence_semantic: "not_configured" };
const source = { id: "28d6ce92-c958-4010-b4ba-1784cf82dc10", source_id: "28d6ce92-c958-4010-b4ba-1784cf82dc10",
  name: "Parsing guide.md", kind: "docs", source_type: "docs", status: "uploaded", documents: 0, document_count: 0,
  chunks: 0, chunk_count: 0, error_message: null, embedding_status: "not_configured", created_at: new Date().toISOString(),
  updated: new Date().toISOString(), detail: "Parsing queued", is_active: true, access_available: true,
  retrieval_available: false, managed_by_connector: false };
let uploaded = false;
let polls = 0;
await context.route("**/api/backend/**", async (route) => {
  const request = route.request();
  const path = new URL(request.url()).pathname;
  report.requests.push({ path, method: request.method() });
  if (path.endsWith("/documents")) {
    uploaded = true;
    return route.fulfill({ status: 202, json: source });
  }
  if (path.endsWith("/sources")) {
    if (!uploaded) return route.fulfill({ json: [] });
    polls += 1;
    source.status = polls > 1 ? "parsed" : "processing";
    source.documents = source.document_count = polls > 1 ? 1 : 0;
    return route.fulfill({ json: [source] });
  }
  return route.fulfill({ json: path.endsWith("/health") ? health : [] });
});
async function check(name, run) {
  await run();
  report.checks.push(name);
  console.log(`PASS ${name}`);
}
async function noOverflow() {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Unexpected horizontal overflow");
}
try {
  await check("Home provides one chat composer with automatic app access and no workflow controls", async () => {
    await page.goto(base, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { level: 1, name: /Your knowledge/ })).toBeVisible();
    const chat = page.getByRole("region", { name: "Workspace assistant" });
    await expect(chat.getByRole("textbox", { name: "Message", exact: true })).toBeEnabled();
    await expect(chat.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
    await expect(chat.getByRole("checkbox")).toHaveCount(0);
    await expect(chat.getByRole("button", { name: "Run workflow", exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "Open workspace", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: /Your knowledge/ })).toBeInViewport();
    await noOverflow();
    await page.screenshot({ path: resolve(output, "parsing-home.png"), fullPage: true });
  });
  await check("Chat offers source upload and app connections without exposing runtime settings", async () => {
    await expect(page.getByRole("link", { name: "Add sources", exact: true })).toHaveAttribute("href", "/sources");
    await expect(page.getByRole("link", { name: "Connect apps", exact: true })).toHaveAttribute("href", "/connectors");
    await expect(page.getByText("Parsing only", { exact: true })).toHaveCount(0);
  });
  await check("Desktop sidebar collapse and theme controls work", async () => {
    await page.locator("aside.ff-sidebar").getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(page.locator("aside.ff-sidebar")).toBeHidden();
    await page.getByRole("button", { name: "Expand sidebar" }).click();
    await expect(page.locator("aside.ff-sidebar")).toBeVisible();
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.screenshot({ path: resolve(output, "workspace-light.png"), fullPage: true });
    await page.getByRole("button", { name: "Toggle theme" }).click();
  });
  await check("Upload acceptance polls through processing to parsed and then stops", async () => {
    await page.goto(`${base}/sources`, { waitUntil: "networkidle" });
    await page.getByLabel("Document title").fill("Parsing guide");
    await page.getByLabel("Documentation content").fill("# Parsing guide\n\nKeep original structure and provenance.");
    await page.getByRole("button", { name: "Add to knowledge base" }).click();
    await expect(page.getByText("Source accepted. Parsing is queued.")).toBeVisible();
    await expect(page.getByText("Parsed", { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/1 stored documents/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry search preparation" })).toHaveCount(0);
    const completedPolls = polls;
    await page.waitForTimeout(2300);
    assert.equal(polls, completedPolls, "Terminal parsing status must stop polling");
    await page.screenshot({ path: resolve(output, "parsing-sources.png"), fullPage: true });
  });
  await check("Settings reports canonical parsing, opt-in indexing and chat configuration", async () => {
    await page.goto(`${base}/settings`, { waitUntil: "networkidle" });
    await expect(page.getByText("Canonical parsing and opt-in evidence indexing", { exact: true })).toBeVisible();
    await expect(page.getByText("Set the backend API key and model", { exact: true })).toBeVisible();
    await expect(page.getByText("Embedding configuration", { exact: true })).toHaveCount(0);
  });
  await check("320px and 390px home and source layouts stay bounded", async () => {
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      for (const path of ["/", "/sources", "/history", "/saved", "/settings", "/connectors"]) {
        await page.goto(base + path, { waitUntil: "networkidle" });
        await noOverflow();
      }
    }
    await page.goto(base, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("dialog", { name: "Workspace menu" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Workspace menu" })).not.toBeVisible();
    await page.screenshot({ path: resolve(output, "parsing-mobile.png"), fullPage: true });
  });
  assert.equal(report.errors.length, 0);
  assert.ok(!report.requests.some((request) => /\/(ask|debug|chat|retry-embedding)$/.test(request.path)));
} catch (error) {
  report.failure = error.message;
  await page.screenshot({ path: resolve(output, "parsing-failure.png"), fullPage: true });
  throw error;
} finally {
  await writeFile(resolve(output, "parsing-results.json"), JSON.stringify(report, null, 2));
  await browser.close();
}
