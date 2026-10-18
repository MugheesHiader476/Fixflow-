/** Actual UI/Three.js; provider HTTP and identity are mocked. No live consent claim. */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const toolRoot = process.env.FIXFLOW_BROWSER_TOOLS;
assert.ok(toolRoot, "Set FIXFLOW_BROWSER_TOOLS to the installed Playwright directory.");
const { chromium } = await import(pathToFileURL(resolve(toolRoot, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(toolRoot, "test.mjs")).href);
const output = resolve(".local/browser-evidence");
await mkdir(output, { recursive: true });
const report = { boundary: "Actual React and Three.js, mocked gateway and identity", checks: [], errors: [] };
const cards = ["gmail", "github", "google_drive", "slack"].map((provider) => ({
  provider, configured: false, setup_message: "Server configuration required.", accounts: [], install_url: null,
}));
const health = { status: "ok", service: "fixflow-api", api: "ok", database: "connected", pgvector: "available",
  schema: "ready", revision: "0006", expected_revision: "0006", sources: 0, documents: 0, chunks: 0,
  embedded_chunks: 0, pending_sources: 0, failed_sources: 0, embedding_configured: false, ai_generation: "removed" };

async function mockGateway(context) {
  await context.route("**/api/backend/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    let json = [];
    if (path.endsWith("/connectors")) json = cards;
    else if (path.endsWith("/health")) json = health;
    return route.fulfill({ json });
  });
}
async function verifyVisuals() {
  const browser = await chromium.launch({
    executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
    await mockGateway(context);
    const page = await context.newPage();
    page.on("pageerror", (error) => report.errors.push(error.message));
    await page.goto("http://127.0.0.1:4173/connectors", { waitUntil: "networkidle" });
    await expect(page.locator(".ff-knowledge-visual canvas")).toBeVisible({ timeout: 20000 });
    const rendered = await page.locator(".ff-knowledge-visual canvas").evaluate((canvas) => {
      const gl = canvas.getContext("webgl2");
      return !!gl && gl.drawingBufferWidth > 0 && gl.getError() === gl.NO_ERROR;
    });
    assert.ok(rendered, "The real 3D canvas must initialize without GL errors");
    await page.screenshot({ path: resolve(output, "connectors-3d.png"), fullPage: true });
    report.checks.push("Real React Three Fiber canvas initializes and renders without GL errors");

    const detachedCanvas = await page.locator(".ff-knowledge-visual canvas").elementHandle();
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.locator(".ff-knowledge-visual canvas")).toHaveCount(0);
    // Renderer teardown can report context loss after an intentional unmount.
    await detachedCanvas.evaluate((canvas) => canvas.dispatchEvent(new Event("webglcontextlost")));
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(page.locator(".ff-knowledge-visual canvas")).toBeVisible({ timeout: 20000 });
    report.checks.push("Intentional canvas teardown does not permanently disable later rendering");
  
    await page.getByRole("textbox", { name: "Search connectors" }).fill("drive");
    await expect(page.getByRole("heading", { name: "Google Drive", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Gmail", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Connected", exact: true }).click();
    await expect(page.getByText(/No apps match/)).toBeVisible();
    report.checks.push("Connector name and connected-state filters preserve empty states");
    await page.getByRole("textbox", { name: "Search connectors" }).fill("");
    await page.getByRole("button", { name: "All apps" }).click();
  
    // The decorative canvas is intentionally unmounted outside the viewport.
    await page.locator(".ff-knowledge-visual").scrollIntoViewIfNeeded();
    await expect(page.locator(".ff-knowledge-visual canvas")).toBeVisible({ timeout: 20000 });
    await page.locator(".ff-knowledge-visual canvas").evaluate((canvas) => canvas.dispatchEvent(new Event("webglcontextlost")));
    await expect(page.locator(".ff-knowledge-visual .ff-visual-fallback")).toBeVisible();
    await expect(page.getByRole("button", { name: "Connect Gmail" })).toBeDisabled();
    report.checks.push("Context loss replaces decorative 3D with a static visual and preserves app controls");
  
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator(".ff-knowledge-visual .ff-visual-fallback")).toBeVisible();
    await expect(page.locator(".ff-knowledge-visual canvas")).toHaveCount(0);
    report.checks.push("Reduced-motion preference prevents loading a 3D canvas");
  
    for (const width of [320, 390, 768, 1024, 1920]) {
      await page.setViewportSize({ width, height: 950 });
      for (const route of ["/", "/connectors"]) {
        await page.goto("http://127.0.0.1:4173" + route, { waitUntil: "networkidle" });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Overflow at " + width + route);
        if (route === "/") {
          await page.locator(".ff-hero").scrollIntoViewIfNeeded();
          const image = page.locator(".ff-hero-image");
          await expect(image).toBeVisible();
          await expect.poll(() => image.evaluate((img) => img.complete && img.naturalWidth > 0),
            { message: "Hero asset must load after entering the viewport" }).toBe(true);
        }
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: resolve(output, "ui-" + (route === "/" ? "home" : "connectors") + "-" + width + ".png"), fullPage: true });
      }
    }
    report.checks.push("Loaded hero image and bounded home/connectors at 320/390/768/1024/1920px");
    assert.deepEqual(report.errors, []);
    await context.close();
  } finally {
    await browser.close();
  }
  const fallbackBrowser = await chromium.launch({
    executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true, args: ["--disable-webgl"],
  });
  try {
    const context = await fallbackBrowser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
    await mockGateway(context);
    const page = await context.newPage();
    page.on("pageerror", (error) => report.errors.push(error.message));
    await page.goto("http://127.0.0.1:4173/connectors", { waitUntil: "networkidle" });
    await expect(page.locator(".ff-knowledge-visual .ff-visual-fallback")).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole("heading", { name: "Google Drive", exact: true })).toBeVisible();
    await expect(page.locator(".ff-knowledge-visual canvas")).toHaveCount(0);
    report.checks.push("WebGL-disabled browser renders a static fallback with usable connector UI");
    assert.deepEqual(report.errors, []);
  } finally {
    await fallbackBrowser.close();
    await writeFile(resolve(output, "visual-results.json"), JSON.stringify(report, null, 2));
  }
  for (const check of report.checks) console.log("PASS " + check);
}

await verifyVisuals().catch(async (error) => {
  await writeFile(resolve(output, "visual-results.json"), JSON.stringify({ ...report, failure: error.message }, null, 2));
  throw error;
});
