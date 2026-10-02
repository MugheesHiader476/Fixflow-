/** Real Connected Apps components with mocked provider/gateway HTTP; no live OAuth claim. */
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
const account = {
  id: "28d6ce92-c958-4010-b4ba-1784cf82dc10", provider: "gmail", display_name: "person@example.test", external_account_id: "external",
  status: "connected", authentication_status: "valid", provider_health: "available", sync_status: "idle", last_sync_at: null,
  last_successful_request: null, error_message: null, progress: {}, created_at: "2026-10-02T00:00:00Z",
  configuration: { resource_ids: [], branches: {}, categories: ["code", "issues"], start_date: null, end_date: null, attachments: false, threads: true, files: false, mime_types: [] },
};
const errors = [], requests = [], checks = [];
const browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", (error) => errors.push(error.message));
let polls = 0;
await context.route("**/api/backend/**", async (route) => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  requests.push({ method: request.method(), path });
  if (path.endsWith("/resources")) {
    const second = url.searchParams.has("cursor");
    return route.fulfill({ json: { resources: [{ id: second ? "SENT" : "INBOX", name: second ? "Sent" : "Inbox", kind: "label", parent_id: null, version: null, metadata: {} }], next_cursor: second ? null : "page-two" } });
  }
  if (path.endsWith("/configure")) {
    account.configuration = request.postDataJSON();
    return route.fulfill({ json: account });
  }
  if (path.endsWith("/sync")) {
    account.sync_status = "pending"; account.progress = { discovered: 2, queued: 0, unchanged: 0, failed: 0, removed: 0 };
    return route.fulfill({ status: 202, json: account });
  }
  if (path.endsWith("/disconnect")) {
    assert.equal(request.postDataJSON().policy, "purge");
    account.status = "disconnected"; account.sync_status = "cancelled";
    return route.fulfill({ json: account });
  }
  if (path.endsWith("/connect")) return route.fulfill({ json: { authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=test-state" } });
  if (path.endsWith("/connectors")) {
    if (account.sync_status === "pending" && ++polls > 1) { account.sync_status = "complete"; account.progress.queued = 2; account.last_sync_at = new Date().toISOString(); }
    return route.fulfill({ json: ["gmail", "github", "google_drive", "slack"].map((provider) => ({ provider, configured: provider === "gmail", setup_message: provider === "gmail" ? null : "Server credentials are required", accounts: provider === "gmail" ? [account] : [], install_url: null })) });
  }
  return route.fulfill({ json: [] });
});
async function check(name, run) { await run(); checks.push(name); console.log("PASS " + name); }
async function noOverflow() { assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Horizontal overflow"); }
try {
  await check("Provider cards, callback notice, connected identity and unconfigured state", async () => {
    await page.goto("http://127.0.0.1:4173/connectors?connected=gmail", { waitUntil: "networkidle" });
    for (const name of ["Gmail", "GitHub", "Google Drive", "Slack"]) await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Connect Slack" })).toBeDisabled();
    await expect(page.getByText("Account connected. Select resources to start synchronization.")).toBeVisible();
    await expect(page.getByText("person@example.test", { exact: true })).toBeVisible();
    await noOverflow();
    await page.screenshot({ path: resolve(output, "connectors-desktop.png"), fullPage: true });
  });
  await check("Native resource dialog, pagination, selection, date and attachment configuration", async () => {
    await page.getByRole("button", { name: "Configure resources" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByLabel("Inbox (label)").check();
    await page.getByRole("button", { name: "Load more resources" }).click();
    await page.getByLabel("Sent (label)").check();
    await page.getByLabel("Include attachments").check();
    await page.getByLabel("From date (UTC)").fill("2026-09-01");
    await page.getByRole("button", { name: "Save selection" }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    assert.deepEqual(account.configuration.resource_ids, ["INBOX", "SENT"]);
    assert.equal(account.configuration.attachments, true);
  });
  await check("Manual synchronization updates via polling with truthful ingestion counts", async () => {
    await page.getByRole("button", { name: "Sync now" }).click();
    await expect(page.getByRole("button", { name: "Configure resources" })).toBeDisabled();
    await expect(page.getByText(/Queued for ingestion: 2/)).toBeVisible({ timeout: 12000 });
    await expect(page.getByRole("button", { name: "Sync now" })).toBeEnabled();
    assert.equal(account.sync_status, "complete");
  });
  await check("Mobile 390px and 320px, modal focus and Escape cancellation", async () => {
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await noOverflow();
      await page.getByRole("button", { name: "Configure resources" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await noOverflow();
      assert.ok(await page.evaluate(() => !!document.activeElement?.closest("dialog")));
      await page.screenshot({ path: resolve(output, `connectors-selection-${width}.png`), fullPage: true });
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).not.toBeVisible();
    }
  });
  await check("Disconnect requires explicit purge confirmation and displays returned status", async () => {
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByLabel("Permanently purge connected sources and chunks").check();
    await expect(page.getByText("Purging cannot be undone.")).toBeVisible();
    await noOverflow();
    await page.screenshot({ path: resolve(output, "connectors-disconnect-mobile.png"), fullPage: true });
    await page.getByRole("button", { name: "Confirm disconnect" }).click();
    await expect(page.getByRole("button", { name: "Reconnect" })).toBeVisible();
    assert.equal(account.status, "disconnected");
  });
  await check("Connect begins an actual provider redirect", async () => {
    await context.route("https://accounts.google.com/**", (route) => route.fulfill({ contentType: "text/html", body: "<p>Provider boundary mocked for browser verification</p>" }));
    await page.getByRole("button", { name: "Connect Gmail" }).click();
    await page.waitForURL("https://accounts.google.com/o/oauth2/v2/auth?state=test-state");
  });
  assert.deepEqual(errors, []);
} finally {
  await writeFile(resolve(output, "connectors-report.json"), JSON.stringify({ mode: "real UI with mocked gateway and provider boundary", checks, errors, requests }, null, 2));
  await browser.close();
}
