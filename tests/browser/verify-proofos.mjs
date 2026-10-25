import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { approvalRecord, packageRecord, projectId, proofWorkspace } from "../frontend/fixtures/proofos.ts";

const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to the external Playwright installation.");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const output = resolve(".local/browser-evidence");
await mkdir(output, { recursive: true });
const base = "http://127.0.0.1:4173";
const report = { boundary: "Actual ProofOS React UI, mocked HTTP/domain fixtures and Clerk identity. No live provider, customer approval or invoice claim.", checks: [], errors: [], requests: [] };
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", (error) => report.errors.push(error.message));
let current = proofWorkspace();
let projects = [];
let approvals = [];
let decided = null;
const health = { status: "ok", service: "fixflow-api", api: "ok", database: "connected", pgvector: "available", schema: "ready", revision: "0011", expected_revision: "0011", sources: 0, documents: 0, chunks: 0, embedded_chunks: 0, pending_sources: 0, failed_sources: 0, embedding_configured: false, ai_generation: "removed" };
await context.route("**/api/backend/**", async (route) => {
  const request = route.request();
  const path = new URL(request.url()).pathname.replace("/api/backend", "");
  report.requests.push({ path, method: request.method() });
  if (path === "/health") return route.fulfill({ json: health });
  if (path === "/api/proofos/projects" && request.method() === "POST") {
    current.project = { ...current.project, ...request.postDataJSON() }; projects = [current.project];
    return route.fulfill({ status: 201, json: current.project });
  }
  if (path === "/api/proofos/projects") return route.fulfill({ json: projects });
  if (path === `/api/proofos/projects/${projectId}`) return route.fulfill({ json: current });
  if (path.endsWith("/review") && path.includes("/requirements/")) {
    const body = request.postDataJSON();
    current.requirements[0] = { ...current.requirements[0], interpretation: body.interpretation, acceptance_criteria: body.acceptance_criteria, ambiguities: body.ambiguities, engineering_links: body.engineering_links, state: body.approve ? "APPROVED" : "NEEDS_REVIEW" };
    assert.ok(!Object.hasOwn(body, "original_text"));
    return route.fulfill({ json: current.requirements[0] });
  }
  if (path.endsWith("/invoice-draft")) {
    assert.equal(current.milestones[0].readiness.eligible, true);
    const invoice = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", milestone_id: current.milestones[0].id, package_id: packageRecord.id, amount_minor: 1000000, currency: "USD", payment_terms: "Net 30 after acceptance", status: "draft", created_at: "2026-10-25T00:00:00Z" };
    current.invoices = [invoice];
    return route.fulfill({ json: invoice });
  }
  if (path === "/api/proofos/approvals") return route.fulfill({ json: approvals });
  if (path === `/api/proofos/approvals/${approvalRecord.id}`) return route.fulfill({ json: { approval: approvals[0], package: { product: "ProofOS", project: "Customer Website", package_version: 1, technical_status: "PASSED", customer_acceptance: "pending", evidence: [] } } });
  if (path.endsWith("/decide")) {
    const body = request.postDataJSON();
    assert.equal(body.expected_hash, approvalRecord.package_hash);
    decided = body;
    approvals[0] = { ...approvals[0], status: body.decision, decided_at: new Date().toISOString() };
    return route.fulfill({ json: approvals[0] });
  }
  return route.fulfill({ json: [] });
});
async function check(name, action) { await action(); report.checks.push(name); console.log(`PASS ${name}`); }
async function bounded() { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Unexpected horizontal overflow"); }
try {
  await check("ProofOS branding and project creation retain the single home chat", async () => {
    await page.goto(base, { waitUntil: "networkidle" });
    await expect(page.getByRole("link", { name: "ProofOS home" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Workspace assistant" }).getByRole("textbox", { name: "Message", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Run workflow", exact: true })).toHaveCount(0);
    await page.goto(base + "/projects", { waitUntil: "networkidle" });
    await expect(page.getByText(/No projects yet/)).toBeVisible();
    await page.getByRole("button", { name: "New project" }).click();
    await page.getByLabel("Project name", { exact: true }).fill("Customer Website");
    await page.getByLabel("Customer name", { exact: true }).fill("Fictional Customer");
    await page.getByRole("button", { name: "Create project", exact: true }).click();
    await expect(page.getByText(/Project created/)).toBeVisible();
    await page.getByRole("link", { name: /Customer Website Fictional Customer/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Customer Website" })).toBeVisible();
  });
  await check("Requirement editing preserves the exact quotation and unresolved-review gate", async () => {
    await page.getByRole("tab", { name: "SRS & requirements" }).click();
    await page.locator("summary").getByText("REQ-001", { exact: true }).click();
    await expect(page.locator("blockquote")).toHaveText("Authentication must pass all mandatory tests.");
    await expect(page.getByRole("button", { name: "Approve requirement", exact: true })).toBeDisabled();
    await page.getByLabel("Proposed interpretation").fill("Owner reviewed the required authentication suite.");
    await page.getByRole("button", { name: "Save review", exact: true }).click();
    await expect(page.getByText("Requirement review saved.")).toBeVisible();
    assert.equal(current.requirements[0].original_text, "Authentication must pass all mandatory tests.");
  });
  await check("Scenario A exposes failing tests, missing documentation and absent acceptance", async () => {
    await page.getByRole("tab", { name: "Milestones", exact: true }).click();
    await expect(page.getByText("Mandatory authentication test failed for the applicable SHA.")).toBeVisible();
    await expect(page.getByText("Delivery documentation is missing.")).toBeVisible();
    await expect(page.getByText("Customer approval is pending", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Prepare invoice draft" })).toBeDisabled();
    await bounded();
    await page.screenshot({ path: resolve(output, "proofos-scenario-a.png"), fullPage: true });
  });
  await check("Scenario B keeps invoice readiness blocked despite passing technical evidence", async () => {
    current.verifications[0].result.technical_status = "PASSED"; current.verifications[0].result.delivery_status = "PASSED"; current.verifications[0].result.checks = [];
    current.milestones[0].readiness = { eligible: false, reasons: ["Customer approval is pending"] };
    await page.getByRole("button", { name: "Refresh project" }).click();
    await expect(page.getByText("PASSED", { exact: true })).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Prepare invoice draft" })).toBeDisabled();
    await page.screenshot({ path: resolve(output, "proofos-scenario-b.png"), fullPage: true });
  });
  await check("Authenticated review reads a customer-safe package and binds the decision hash", async () => {
    approvals = [structuredClone(approvalRecord)];
    await page.goto(base + "/approvals", { waitUntil: "networkidle" });
    await expect(page.getByRole("button", { name: "Accept package" })).toBeDisabled();
    await page.getByRole("button", { name: "Review customer package" }).click();
    await expect(page.getByRole("link", { name: "Download customer PDF" })).toHaveAttribute("href", `/api/backend/api/proofos/approvals/${approvalRecord.id}/download/pdf`);
    await page.getByLabel("Decision reason").fill("I accept this exact package version.");
    await page.getByRole("button", { name: "Accept package" }).click();
    await expect(page.getByText(/Your accepted decision was recorded/)).toBeVisible();
    assert.equal(decided.expected_hash, approvalRecord.package_hash);
  });
  await check("Scenario C offers only an invoice draft after server-confirmed prerequisites", async () => {
    current.packages = [{ ...packageRecord, internal_review: "approved", customer_status: "accepted" }]; current.approvals = approvals;
    current.milestones[0].readiness = { eligible: true, reasons: [] };
    await page.goto(`${base}/projects/${projectId}`, { waitUntil: "networkidle" });
    await page.getByRole("tab", { name: "Milestones", exact: true }).click();
    await expect(page.getByRole("button", { name: "Prepare invoice draft" })).toBeEnabled();
    await page.getByRole("button", { name: "Prepare invoice draft" }).click();
    await expect(page.getByText(/No invoice has been sent/)).toBeVisible();
    await page.screenshot({ path: resolve(output, "proofos-scenario-c.png"), fullPage: true });
  });
  await check("Project tabs and customer review remain bounded at 320px and 390px", async () => {
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`${base}/projects/${projectId}`, { waitUntil: "networkidle" });
      for (const label of ["Overview", "SRS & requirements", "Milestones", "Evidence", "Packages & approvals", "Activity"]) {
        await page.getByRole("tab", { name: label, exact: true }).click(); await bounded();
      }
      await page.goto(base + "/approvals", { waitUntil: "networkidle" }); await bounded();
    }
    await page.screenshot({ path: resolve(output, "proofos-mobile.png"), fullPage: true });
  });
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = error.message;
  await page.screenshot({ path: resolve(output, "proofos-failure.png"), fullPage: true });
  throw error;
} finally {
  await writeFile(resolve(output, "proofos-results.json"), JSON.stringify(report, null, 2));
  await browser.close();
}
