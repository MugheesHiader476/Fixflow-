/** Actual React chat; mocked Clerk/gateway/model receipts. No live answer-quality claim. */
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
const nextId = "22222222-2222-4222-8222-222222222222";
const evidence = "e-" + "a".repeat(32);
const completed = {
  id, parent_run_id: null, question: "What is blocking the release?", status: "completed", steps: 4,
  usage: { model_calls: 4, input_tokens: 400, output_tokens: 120 }, error: null, created_at: "2026-10-19T10:00:00Z",
  answer: { answer: "The release is waiting on payment integration tests.", claims: [{ text: "The team reported that payment tests are failing.", citations: [{ evidence_id: evidence, quote: "Payment tests are failing." }] }], limitations: ["Based on the selected sources. Deployment status has not been verified."] },
  materials: [{ id: evidence, title: "Release discussion · Slack", text: "Payment tests are failing.", reference: { source_id: id }, observed_at: "2026-10-19T10:00:00Z", warning: null }],
  actions: [], files: [],
};
const submissions = [];
const followed = { ...completed, id: nextId, parent_run_id: id, question: "What should we do next?" };
const cards = ["gmail", "github", "google_drive", "slack"].map((provider) => ({ provider, configured: true, setup_message: null, install_url: null, accounts: [] }));
await page.route("**/api/backend/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path.endsWith("/connectors")) return route.fulfill({ json: cards });
  if (path.endsWith("/chat/messages") && route.request().method() === "POST") {
    const request = route.request().postDataJSON(); submissions.push(request);
    assert.deepEqual(Object.keys(request).sort(), ["parent_message_id", "question"]);
    const response = request.parent_message_id ? followed : completed;
    return route.fulfill({ status: 202, json: response });
  }
  if (path.endsWith("/thread")) return route.fulfill({ json: path.includes(nextId) ? [completed, followed] : [completed] });
  if (path.endsWith("/chat/messages")) return route.fulfill({ json: [completed] });
  if (path.includes("/chat/messages/")) return route.fulfill({ json: completed });
  return route.fulfill({ json: [] });
});
const checks = [];
async function check(name, run) {
  await run(); checks.push(name);
}
try {
  for (const width of [320, 390, 1280]) {
    await check(`Prompt, citations, automatic follow-up, new chat and bounded layout at ${width}px`, async () => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:4173/", { waitUntil: "networkidle" });
    const panel = page.getByRole("region", { name: "Workspace assistant" });
    await expect(panel.getByRole("checkbox")).toHaveCount(0);
    await expect(panel.getByText("Run workflow", { exact: true })).toHaveCount(0);
    await panel.getByRole("textbox", { name: "Message", exact: true }).fill(completed.question);
    await panel.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(panel.getByText(completed.answer.answer)).toBeVisible();
    assert.equal(submissions.at(-1).parent_message_id, null);
    await expect(panel.getByRole("link", { name: "[1]", exact: true })).toHaveAttribute("href", `#source-${id}-${evidence}`);
    await panel.getByText("1 consulted source", { exact: true }).click();
    await panel.locator(`#source-${id}-${evidence} summary`).click();
    await expect(panel.getByText(completed.materials[0].text, { exact: true })).toBeVisible();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow at ${width}px`);
    await page.screenshot({ path: resolve(output, `chat-${width}.png`), fullPage: true });
    if (width === 1280) await page.screenshot({ path: resolve(output, "chat-preview.png"), fullPage: false });
    await panel.getByRole("textbox", { name: "Message", exact: true }).fill("What should we do next?");
    await panel.getByRole("textbox", { name: "Message", exact: true }).press("Enter");
    await expect(panel.getByRole("article", { name: "Chat message" })).toHaveCount(2);
    assert.equal(submissions.at(-1).parent_message_id, id);
    await panel.getByRole("button", { name: "New chat", exact: true }).click();
    await expect(panel.getByRole("article", { name: "Chat message" })).toHaveCount(0);
    });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ boundary: "Actual React chat with mocked gateway/model receipts", checks }));
} finally {
  await writeFile(resolve(output, "chat-report.json"), JSON.stringify({ checks, errors }, null, 2));
  await browser.close();
}
