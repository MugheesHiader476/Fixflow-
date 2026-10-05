/** Core audit: real Next signed-out checks; real API/DB through the test-only UI harness. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

assert.ok(process.env.FIXFLOW_BROWSER_TOOLS, "Set FIXFLOW_BROWSER_TOOLS to an installed Playwright directory");
const { chromium } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "test.mjs")).href);
const output = resolve(".local/audit");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const ui = "http://127.0.0.1:4173";
const api = `${ui}/api/backend/api`;
const production = process.env.FIXFLOW_NEXT_TEST_URL || "http://127.0.0.1:3000";
let report = {
  mode: "Production Next for signed-out checks; mocked Clerk/Next navigation ONLY for authenticated component checks; live disposable PostgreSQL/backend",
  checks: [],
  blocked: ["Authenticated production Clerk session unavailable", "Cross-account E2E: second real authenticated Clerk test user unavailable"],
};
if (process.env.FIXFLOW_AUDIT_ONLY) {
  report = JSON.parse(await readFile(resolve(output, "browser-audit.json"), "utf8"));
}

async function check(name, run) {
  if (process.env.FIXFLOW_AUDIT_ONLY && name !== process.env.FIXFLOW_AUDIT_ONLY) return;
  report.checks = report.checks.filter((previous) => previous.name !== name);
  await context.tracing.start({ screenshots: true, snapshots: true });
  try {
    await run();
    report.checks.push({ name, status: "PASS" });
    console.log("PASS " + name);
    await context.tracing.stop();
  } catch (error) {
    report.checks.push({ name, status: "FAIL", error: error.message });
    console.error("FAIL " + name + ": " + error.message);
    const safeName = name.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
    await page.screenshot({ path: resolve(output, `${safeName}.png`), fullPage: true });
    await context.tracing.stop({ path: resolve(output, `${safeName}.zip`) });
    process.exitCode = 1;
  }
}

async function completed(id, status = "ready_for_embedding") {
  await expect.poll(async () => {
    const response = await context.request.get(`${api}/sources/${id}`);
    assert.equal(response.status(), 200);
    return (await response.json()).status;
  }, { timeout: 30000 }).toBe(status);
  return (await (await context.request.get(`${api}/sources/${id}`)).json());
}

let sourceId;
try {
  await check("Production signed-out protected page redirects to sign-up", async () => {
    const response = await context.request.get(`${production}/sources`, { maxRedirects: 0 });
    assert.ok([302, 303, 307, 308].includes(response.status()));
    assert.ok(new URL(response.headers().location, production).pathname.startsWith("/sign-up"));
    report.signedOutRedirect = { status: response.status(), location: response.headers().location };
    try {
      await page.goto(`${production}/sources`, { waitUntil: "commit", timeout: 10000 });
      assert.ok(new URL(page.url()).pathname.startsWith("/sign-up"));
    } catch (error) {
      if (!error.message.includes("Timeout")) throw error;
      report.blocked.push("Clerk sign-up browser landing timed out; production HTTP 307 redirect verified independently");
    }
  });
  await check("Production private gateway rejects unauthenticated and forged browser identity", async () => {
    assert.equal((await context.request.get(`${production}/api/backend/api/sources`)).status(), 401);
    const forged = await context.request.get(`${production}/api/backend/api/sources`, {
      headers: { Authorization: "Bearer forged", "X-FixFlow-User-Id": "user_browser_test" },
    });
    assert.equal(forged.status(), 401);
  });
  await check("UI uploads controlled Markdown and real worker completes ingestion", async () => {
    await page.goto(`${ui}/sources`);
    await page.getByRole("tab", { name: /^Upload a file/ }).click();
    await page.getByLabel("Upload document").setInputFiles({
      name: "pipeline-audit.md", mimeType: "text/markdown", buffer: await readFile(resolve("backend/tests/fixtures/pipeline/integrity-audit.md")),
    });
    const accepted = page.waitForResponse((response) => response.url() === `${api}/documents` && response.request().method() === "POST");
    await page.getByRole("button", { name: "Add to knowledge base" }).click();
    const response = await accepted;
    assert.equal(response.status(), 202);
    sourceId = (await response.json()).id;
    const source = await completed(sourceId);
    assert.ok(source.chunk_count > 0);
    report.source = source;
    await expect(page.getByText("pipeline-audit.md", { exact: true })).toBeVisible();
  });
  await check("Source and retrieval-ready status persist after reload", async () => {
    assert.ok(sourceId);
    await page.reload();
    await expect(page.getByText("pipeline-audit.md", { exact: true })).toBeVisible();
    await completed(sourceId);
  });
  for (const [position, term] of [["beginning", "ZXQBEGIN47391MD"], ["middle", "ZXQMIDDLE47391MD"], ["end", "ZXQEND47391MD"], ["boundary", "ZXQBOUNDARY47391MD"]]) {
    await check(`API exact ${position} term retrieves expected source`, async () => {
      const response = await context.request.post(`${api}/debug`, { data: { error: term } });
      assert.equal(response.status(), 200);
      const values = (await response.json()).sources;
      assert.equal(values[0]?.title, "pipeline-audit.md");
      assert.ok(values[0].excerpt.includes(term));
      report.retrieval ??= [];
      report.retrieval.push({ position, query: term, chunk: values[0].id, rank: 1 });
    });
  }
  await check("Heading-only exact term retrieves expected source", async () => {
    const response = await context.request.post(`${api}/debug`, { data: { error: "ZXQHEADING47391" } });
    assert.equal(response.status(), 200);
    assert.ok((await response.json()).sources.some((source) => source.title === "pipeline-audit.md"), "Heading exists in chunk context but not in the keyword index");
  });
  await check("Real ingestion failure is persisted and visible in UI", async () => {
    const response = await context.request.post(`${api}/documents`, {
      multipart: { file: { name: "invalid-audit.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\nThis is deliberately incomplete harmless PDF data.") } },
    });
    assert.equal(response.status(), 202);
    const failed = await completed((await response.json()).id, "failed");
    assert.ok(failed.error_message);
    assert.equal(failed.chunk_count, 0);
    assert.ok(!/Traceback|postgresql:|token|secret/i.test(failed.error_message));
    await page.goto(`${ui}/sources`);
    await expect(page.getByText("invalid-audit.pdf", { exact: true })).toBeVisible();
    await expect(page.getByText("Failed", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("invalid-audit.pdf", { exact: true })).toBeVisible();
  });
  await check("Document HTML and JavaScript remain inert in retrieved UI evidence", async () => {
    const term = "ZXQINERTAUDIT47391";
    const payload = `ZXQINERTPAYLOAD47391\n<script>window.__fixflow_audit_executed = true</script>\n<img id="audit-untrusted-image" src="x" onerror="window.__fixflow_audit_executed = true">`;
    const response = await context.request.post(`${api}/documents`, {
      multipart: { file: { name: "inert-audit.md", mimeType: "text/markdown", buffer: Buffer.from(`# Inert audit\n\n${term} authorized evidence.\n\n\`\`\`html\n${payload}\n\`\`\`\n`) } },
    });
    assert.equal(response.status(), 202);
    await completed((await response.json()).id);
    await page.goto(ui);
    await page.getByRole("textbox", { name: "Error message" }).fill("ZXQINERTPAYLOAD47391");
    const result = page.waitForResponse((response) => response.url() === `${api}/debug` && response.request().method() === "POST");
    await page.getByRole("button", { name: "Search documentation", exact: true }).click();
    const values = (await (await result).json()).sources;
    assert.ok(values.some((source) => source.excerpt.includes(payload)), "The actual malicious-looking text must reach the UI rendering boundary");
    await expect(page.getByRole("region", { name: "Evidence and sources" })).toBeVisible();
    assert.equal(await page.evaluate(() => window.__fixflow_audit_executed), undefined);
    assert.equal(await page.locator("#audit-untrusted-image").count(), 0);
  });
  await check("UI accepts backend-supported XLSX document", async () => {
    await page.goto(`${ui}/sources`);
    await page.getByRole("tab", { name: /^Upload a file/ }).click();
    await page.getByLabel("Upload document").setInputFiles(resolve("backend/tests/fixtures/pipeline/workbook.xlsx"));
    await expect(page.getByRole("button", { name: "Add to knowledge base" })).toBeEnabled();
    const accepted = page.waitForResponse((response) => response.url() === `${api}/documents` && response.request().method() === "POST");
    await page.getByRole("button", { name: "Add to knowledge base" }).click();
    const response = await accepted;
    assert.equal(response.status(), 202);
    const source = await completed((await response.json()).id);
    assert.ok(source.chunk_count > 0);
    const found = await context.request.post(`${api}/debug`, { data: { error: "Timeout" } });
    assert.equal(found.status(), 200);
    assert.ok((await found.json()).sources.some((item) => item.title === "workbook.xlsx"));
  });
} finally {
  await writeFile(resolve(output, "browser-audit.json"), JSON.stringify(report, null, 2) + "\n");
  await browser.close();
}
