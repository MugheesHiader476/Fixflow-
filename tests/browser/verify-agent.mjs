/** Actual React UI; mocked Clerk/gateway. No provider, model or mail calls. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to the existing Playwright package directory.");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const output = resolve(".local/browser-evidence");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const id = "11111111-1111-4111-8111-111111111111";
const evidence = "e-" + "a".repeat(32);
const action = { id, account_id: id, status: "draft", payload: { recipient: "manager@example.com", subject: "Release update", body: "Payment tests are failing." }, payload_hash: "b".repeat(64), provider_message_id: null, error: null };
const completed = { id, question: "Review project status and prepare a reply", status: "completed", steps: 4, usage: { model_calls: 4, input_tokens: 400, output_tokens: 120 }, error: null, created_at: "2026-10-17T10:00:00Z", answer: { answer: "Here is the observed status.", claims: [{ text: "Payment tests are failing.", citations: [{ evidence_id: evidence, quote: "Payment tests are failing." }] }], limitations: ["Selected sources only."] }, materials: [{ id: evidence, title: "Manager feedback", text: "Payment tests are failing.", reference: { source_id: id }, observed_at: "2026-10-17T10:00:00Z", warning: null }], actions: [action], files: [{ id, filename: "SRS.pdf", mime_type: "application/pdf", download_path: `/api/agent/files/${id}/download` }] };
const submissions = [];
let sends = 0;
await page.route("**/api/backend/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.endsWith("/execute")) {
    assert.deepEqual(route.request().postDataJSON(), { expected_hash: action.payload_hash });
    sends++;
    return route.fulfill({ json: { ...action, status: "sent", provider_message_id: "mock-receipt" } });
  }
  if (path.endsWith("/agent/runs") && route.request().method() === "POST") {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ status: 202, json: completed });
  }
  if (path.endsWith("/agent/runs")) return route.fulfill({ json: [completed] });
  if (path.includes("/agent/runs/")) return route.fulfill({ json: completed });
  return route.fulfill({ json: [] });
});
const checks = [];
async function check(name, run) {
  await run();
  checks.push(name);
}
try {
  for (const width of [320, 390, 1280]) {
    await check(`Draft review, exact send, citations, private file and no overflow at ${width}px`, async () => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:4173/", { waitUntil: "networkidle" });
    const panel = page.getByRole("region", { name: "Workspace assistant" });
    await panel.getByRole("checkbox", { name: "Allow email drafts and replies for this request" }).check();
    await panel.getByRole("textbox", { name: "Task", exact: true }).fill(completed.question);
    await panel.getByRole("button", { name: "Run workflow", exact: true }).click();
    await expect(panel.getByText(completed.answer.answer)).toBeVisible();
    assert.equal(submissions.at(-1).action_mode, "draft");
    assert.deepEqual(submissions.at(-1).allowed_recipients, []);
    assert.equal(sends, checks.length);
    await panel.locator(`#${evidence} summary`).click();
    await expect(panel.getByRole("link", { name: "[1]", exact: true })).toHaveAttribute("href", `#${evidence}`);
    await expect(panel.getByRole("link", { name: "SRS.pdf" })).toHaveAttribute("href", `/api/backend/api/agent/files/${id}/download`);
    await panel.getByRole("button", { name: "Send this exact email" }).click();
    await expect(panel.getByText("Gmail receipt: mock-receipt")).toBeVisible();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}px`);
    await page.screenshot({ path: resolve(output, `agent-${width}.png`), fullPage: true });
    });
  }
  assert.equal(sends, 3);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ boundary: "Actual React UI with mocked Clerk/gateway; no live send", checks }));
} finally {
  await writeFile(resolve(output, "agent-report.json"), JSON.stringify({ checks, errors }, null, 2));
  await browser.close();
}
