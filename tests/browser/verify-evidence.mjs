/** Actual evidence UI, mocked gateway/Clerk; independent of live keys and application data. */
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
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const sourceId = "11111111-1111-4111-8111-111111111111";
const passageId = "p-" + "a".repeat(40);
const source = { id: sourceId, source_id: sourceId, name: "Weekly Report", kind: "docs", source_type: "docs", status: "parsed", documents: 1, document_count: 1, chunks: 0, chunk_count: 0, error_message: null, created_at: "2026-10-16T12:00:00Z", updated: "2026-10-16T12:00:00Z", detail: "Parsed", access_available: true };
const answer = { status: "answered", text: "The release is delayed because payment testing is incomplete.", claims: [{ text: "The release is delayed because payment testing is incomplete.", citations: [{ passage_id: passageId, quote: "Payment testing is incomplete." }] }], elapsed_ms: 30, input_tokens: 400, output_tokens: 60, model: "mock-only", evidence: { coverage: "project_indexed_snapshots", warnings: ["Search covers indexed snapshots, not a live provider scan."], elapsed_ms: 20, evidence_tokens: 45, model_calls: 1, embedding_calls: 1, embedding_input_tokens: 12, retrieval: "hybrid", passages: [{ score: 1, reason: "match", passage: { id: passageId, source_id: sourceId, source_hash: "a".repeat(64), canonical_hash: "b".repeat(64), ordinal: 0, kind: "prose", tokens: 45, prefix: "Project: Atlas\nSource: Weekly Report\nSection: Release blockers\nDate: 2026-10-16", content: "Payment testing is incomplete.", warnings: [], spans: [{ block_id: "b-original", start: 0, end: 30, unit: "characters", provenance: { page: 2, line_start: null, line_end: null, sheet: null, cell_range: null } }] } }] } };
let answerRequests = 0;
await page.route("**/api/backend/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.endsWith("/evidence/answer")) {
    answerRequests++;
    const body = route.request().postDataJSON();
    assert.equal(body.project, "Atlas");
    assert.equal(body.synthesize, true);
    assert.equal(body.retrieval, "hybrid");
    return route.fulfill({ json: answer });
  }
  if (path.endsWith("/embed")) return route.fulfill({ json: { source_id: sourceId, prepared: 1, remaining: 0, embedding_calls: 1, input_tokens: 45, elapsed_ms: 12, status: "complete", active: true, error: null } });
  if (path.endsWith("/index")) return route.fulfill({ json: { source_id: sourceId, project: "Atlas", passages: 1 } });
  if (path.endsWith("/sources")) return route.fulfill({ json: [source] });
  return route.fulfill({ json: [] });
});
const checks = [];
async function check(name, run) {
  await run();
  checks.push(name);
}
try {
  for (const width of [320, 390, 1280]) {
    await check(`Index, explicit synthesis, citation, source span and no overflow at ${width}px`, async () => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:4173/", { waitUntil: "networkidle" });
    const panel = page.getByRole("region", { name: "Project evidence" });
    await panel.getByRole("textbox", { name: "Project", exact: true }).fill("Atlas");
    await panel.getByText("Add a parsed source to this project").click();
    await panel.getByRole("button", { name: "Refresh parsed sources" }).click();
    await panel.getByRole("combobox", { name: "Source", exact: true }).selectOption(sourceId);
    await panel.getByRole("button", { name: "Add source to project" }).click();
    await expect(panel.getByRole("status")).toContainText("1 passages added to Atlas.");
    await panel.getByRole("button", { name: "Prepare semantic search" }).click();
    await expect(panel.getByRole("status")).toContainText("1 passages prepared; 0 remaining.");
    await panel.getByRole("combobox", { name: "Search mode" }).selectOption("hybrid");
    await panel.getByRole("textbox", { name: "Question" }).fill("Why is the release delayed?");
    await panel.getByRole("checkbox").check();
    await panel.getByRole("button", { name: "Search evidence" }).click();
    await expect(panel.locator("p").filter({ hasText: answer.claims[0].text }).first()).toBeVisible();
    await panel.locator(`#${passageId} summary`).click();
    await expect(panel.getByText("b-original: characters 0–30 · page 2")).toBeVisible();
    await expect(panel.getByRole("link", { name: "[1]", exact: true })).toHaveAttribute("href", `#${passageId}`);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}px`);
    await page.screenshot({ path: resolve(output, `evidence-${width}.png`), fullPage: true });
    });
  }
  assert.equal(answerRequests, 3);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ boundary: "Actual React components; mocked gateway/Clerk/model response", checks }));
} finally {
  await writeFile(resolve(output, "evidence-report.json"), JSON.stringify({ checks, errors }, null, 2));
  await browser.close();
}
