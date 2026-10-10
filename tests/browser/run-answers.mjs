/** Real Next → FastAPI → PostgreSQL → ingestion/embedding → Ollama answers.
 * Only external Clerk identity is simulated, in an isolated checkout without env files.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { cp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve("."), output = resolve(".local/answers"), staging = resolve(output, "app");
const production = process.env.FIXFLOW_BROWSER_PRODUCTION === "true";
const database = process.env.TEST_DATABASE_URL;
const databaseName = /^postgresql(?:\+asyncpg)?:\/\/[^?]*\/([^/?]+)(?:\?.*)?$/.exec(database ?? "")?.[1];
assert.ok(databaseName?.endsWith("_test"), "Set a separate disposable TEST_DATABASE_URL ending _test");
assert.ok(process.env.FIXFLOW_BROWSER_TOOLS, "Set FIXFLOW_BROWSER_TOOLS to the installed Playwright package directory");
const { chromium } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "index.mjs")).href);
const { expect } = await import(pathToFileURL(resolve(process.env.FIXFLOW_BROWSER_TOOLS, "test.mjs")).href);
const pin = JSON.parse(await readFile(resolve(root, "configs/embedding-winner.json"), "utf8"));
const answerConfig = Object.fromEntries((await readFile(resolve(root, "configs/answers.env.example"), "utf8"))
  .split("\n").filter((line) => line && !line.startsWith("#")).map((line) => line.split("=")));
if (process.env.GENERATION_OLLAMA_URL) answerConfig.GENERATION_OLLAMA_URL = process.env.GENERATION_OLLAMA_URL;
if (process.env.GENERATION_MODEL || process.env.GENERATION_MODEL_DIGEST) {
  assert.ok(process.env.GENERATION_MODEL && process.env.GENERATION_MODEL_DIGEST, "Candidate model and digest must be supplied together");
  answerConfig.GENERATION_MODEL = process.env.GENERATION_MODEL;
  answerConfig.GENERATION_MODEL_DIGEST = process.env.GENERATION_MODEL_DIGEST;
}
const ui = "http://127.0.0.1:3013", api = `${ui}/api/backend/api`;
const environment = {
  ...process.env, ...answerConfig, DATABASE_URL: database, FIXFLOW_DATA_DIR: resolve(output, "uploads"),
  FIXFLOW_API_TOKEN: "test-only-answer-gateway-token-at-least-32-characters",
  OLLAMA_URL: process.env.OLLAMA_URL || answerConfig.GENERATION_OLLAMA_URL, EMBEDDING_MODEL: pin.model, EMBEDDING_DIM: String(pin.dimension),
  EMBEDDING_MODEL_DIGEST: pin.model_digest, EMBEDDING_PROFILE: pin.formatting_version,
  EMBEDDING_API_URL: "", EMBEDDING_API_KEY: "", EMBEDDING_AUTO_PROCESS: "true", EMBEDDING_WORKERS: "1",
  RETRIEVAL_MODE: "dense", RETRIEVAL_TIMEOUT_SECONDS: process.env.RETRIEVAL_TIMEOUT_SECONDS || "10", EMBEDDING_TIMEOUT_SECONDS: "120",
  CONNECTORS__PUBLIC_URL: ui, FRONTEND_ORIGINS: ui, PIPELINE__OCR_ENABLED: "false", PYTHONPATH: root,
};
await mkdir(output, { recursive: true });
const commands = [];
function command(executable, args, options = {}) {
  const child = spawn(executable, args, { cwd: root, env: environment, ...options });
  let log = "";
  child.stdout.on("data", (chunk) => { log += chunk; });
  child.stderr.on("data", (chunk) => { log += chunk; });
  child.result = once(child, "exit").then(([code]) => ({ code, log }));
  commands.push(child);
  return child;
}
async function run(executable, args) {
  const result = await command(executable, args).result;
  assert.equal(result.code, 0, result.log);
}
async function freePort(port) {
  const server = createServer();
  await new Promise((ok, fail) => { server.once("error", fail); server.listen(port, "127.0.0.1", ok); });
  await new Promise((ok) => server.close(ok));
}
await freePort(3013); await freePort(8013);
const python = resolve(root, "myenev/bin/python");
await run(python, ["-m", "alembic", "upgrade", "head"]);
await run(python, ["tests/browser/preembedding.py", "reset", "database"]);
await mkdir(staging, { recursive: true });
await rm(resolve(staging, ".next"), { recursive: true, force: true });
for (const name of ["src", "public", "package.json", "tsconfig.json", "postcss.config.mjs", "next.config.ts"]) {
  await cp(resolve(root, name), resolve(staging, name), { recursive: true, force: true });
}
try { await symlink(resolve(root, "node_modules"), resolve(staging, "node_modules"), "dir"); }
catch (error) { if (error.code !== "EEXIST") throw error; }
const configPath = resolve(staging, "next.config.ts"), config = await readFile(configPath, "utf8");
await writeFile(configPath, config.replace("export default nextConfig;", `
nextConfig.webpack = (config) => {
  config.resolve.alias["@clerk/nextjs/server$"] = ${JSON.stringify(resolve(root, "tests/browser/clerk-server.mjs"))};
  config.resolve.alias["@clerk/nextjs$"] = ${JSON.stringify(resolve(root, "tests/browser/clerk-client.tsx"))};
  return config;
};
export default nextConfig;`));
if (production) await writeFile(configPath, (await readFile(configPath, "utf8"))
  .replace("export default nextConfig;", "nextConfig.output = undefined;\nexport default nextConfig;"));
const report = { model: answerConfig.GENERATION_MODEL, digest: answerConfig.GENERATION_MODEL_DIGEST,
  scope: "Real local models, Next gateway, FastAPI, PostgreSQL and workers; external Clerk identity simulated",
  checks: [], answers: [], errors: [] };
let backend, frontend, browser;
async function check(name, work) {
  const started = performance.now();
  try { await work(); report.checks.push({ name, result: "PASS", seconds: (performance.now() - started) / 1000 }); console.log("PASS " + name); }
  catch (error) { report.checks.push({ name, result: "FAIL", error: error.message }); throw error; }
}
try {
  if (production) await run(process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), "build", staging, "--webpack"]);
  backend = command(python, ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8013"]);
  await expect.poll(async () => { try { return (await fetch("http://127.0.0.1:8013/health")).status; } catch { return 0; } }, { timeout: 30000 }).toBe(200);
  frontend = command(process.execPath, [resolve(root, "node_modules/next/dist/bin/next"), production ? "start" : "dev", staging,
    ...(!production ? ["--webpack"] : []), "--hostname", "127.0.0.1", "--port", "3013"], {
    cwd: staging, env: { ...environment, INTERNAL_API_URL: "http://127.0.0.1:8013", NEXT_PUBLIC_API_URL: "http://127.0.0.1:8013", NEXT_TELEMETRY_DISABLED: "1" },
  });
  await expect.poll(async () => { try { return (await fetch(`${ui}/sign-up`)).status; } catch { return 0; } }, { timeout: 90000 }).toBe(200);
  browser = await chromium.launch({ executablePath: process.env.FIXFLOW_CHROME || "/usr/bin/google-chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  assert.equal((await context.request.post(`${api}/ask`, { data: { question: "Private policy?" } })).status(), 401);
  await context.addCookies([{ name: "fixflow-test-identity", value: "user_preembedding_a", url: ui }]);
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push(error.message));
  const sources = [];
  for (const [name, content] of [
    ["refund-policy.txt", "Refund requests must be made within 30 days of purchase. Shipping fees are not refundable. Opened packages are ineligible for a refund. Unopened packages qualify for a refund when requested within 30 days of purchase. For eligible refunds, contact support with the order number."],
    ["revenue.csv", "month,product,revenue\nApril,Orion,4200\nMay,Orion,5100\n"],
    ["retention.json", '{"retention_days": 7, "region": "EU", "product": "Atlas"}'],
    ["order-luna.txt", "Order Luna: the refund was requested 20 days after purchase. The package is unopened."],
  ]) {
    await check(`Browser upload and real embedding: ${name}`, async () => {
      await page.goto(`${ui}/sources`);
      await page.getByRole("tab", { name: /^Upload a file/ }).click();
      await page.getByLabel("Upload document").setInputFiles({ name, mimeType: "application/octet-stream", buffer: Buffer.from(content) });
      const accepted = page.waitForResponse((response) => response.url() === `${api}/documents` && response.request().method() === "POST");
      await page.getByRole("button", { name: "Add to knowledge base" }).click();
      const response = await accepted; assert.equal(response.status(), 202);
      const source = await response.json(); sources.push(source);
      await expect.poll(async () => (await (await context.request.get(`${api}/sources/${source.id}`)).json()).status, { timeout: 120000 }).toBe("indexed");
      await expect(page.getByText("Ready to ask", { exact: true }).first()).toBeVisible();
    });
  }
  const ask = async (question) => {
    await page.goto(ui);
    await page.getByLabel("Your question").fill(question);
    const answered = page.waitForResponse((response) => response.url() === `${api}/ask` && response.request().method() === "POST", { timeout: 125000 });
    await page.getByRole("button", { name: "Ask question", exact: true }).click();
    const response = await answered;
    assert.equal(response.status(), 200, JSON.stringify(await response.json()));
    const result = await response.json(); report.answers.push({ question, result });
    assert.equal(result.rag.retrievalMethod, "dense");
    assert.equal(result.confidence, null);
    await expect(page.getByRole("region", { name: "Evidence-grounded answer" }).first()).toBeVisible();
    return result;
  };
  let policy;
  await check("Policy answer has exact quotes, owner source IDs and persisted citations", async () => {
    policy = await ask("What is the refund deadline, and are shipping fees refundable?");
    assert.equal(policy.answer.status, "answered"); assert.match(policy.answer.text, /30/);
    assert.match(policy.answer.text, /not refundable|non.refundable|cannot.*refund/i);
    for (const citation of policy.answer.citations) {
      assert.ok(citation.excerpt.includes(citation.quote)); assert.equal(citation.source_id, sources[0].id);
    }
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
    const saved = await (await context.request.get(`${api}/saved`)).json();
    assert.deepEqual(saved[0].sources.map((citation) => citation.quote), policy.answer.citations.map((citation) => citation.quote));
    await page.reload(); await expect(page.getByText(policy.answer.text, { exact: true })).toBeVisible();
    await page.screenshot({ path: resolve(output, "policy-answer.png"), fullPage: true });
  });
  await check("Contextual follow-up uses fresh evidence and survives reload", async () => {
    await page.getByLabel("Follow-up question").fill("What about the shipping fees?");
    const response = page.waitForResponse((item) => item.url() === `${api}/chat` && item.request().method() === "POST", { timeout: 125000 });
    await page.getByRole("button", { name: "Send follow-up" }).click();
    const reply = await response; assert.equal(reply.status(), 200, JSON.stringify(await reply.json()));
    const result = await reply.json(); assert.equal(result.answer.status, "answered");
    report.answers.push({ question: "What about the shipping fees?", result });
    await page.reload(); await expect(page.getByText(result.text, { exact: true })).toBeVisible();
  });
  await check("CSV answer retains the exact row value", async () => {
    const result = await ask("What was the April revenue for Orion?");
    assert.equal(result.answer.status, "answered"); assert.match(result.answer.text, /4,?200/);
    assert.doesNotMatch(result.answer.text, /\$|USD|dollars/i);
    assert.ok(result.answer.citations.some((citation) => citation.source_id === sources[1].id));
  });
  await check("JSON answer retains the configured region and retention", async () => {
    const result = await ask("What is Atlas retention_days in the EU region?");
    assert.equal(result.answer.status, "answered"); assert.match(result.answer.text, /7|seven/i);
    assert.ok(result.answer.citations.some((citation) => citation.source_id === sources[2].id));
  });
  await check("Cross-document reasoning applies the policy to the order and cites both premises", async () => {
    const result = await ask("Is order Luna eligible for a refund under the policy? Explain why.");
    assert.equal(result.answer.status, "answered");
    assert.match(result.answer.text, /eligible|qualif|within.*window/i);
    assert.doesNotMatch(result.answer.text, /(?:Luna|order)\s+(?:is|would be)\s+(?:not eligible|ineligible)/i);
    assert.match(result.answer.text, /20|twenty/i); assert.match(result.answer.text, /30|thirty/i);
    assert.match(result.answer.text, /unopened/i);
    assert.ok(result.answer.citations.some((citation) => citation.source_id === sources[0].id));
    assert.ok(result.answer.citations.some((citation) => citation.source_id === sources[3].id));
    assert.match(result.answer.text, /Conclusion:/);
    const premises = result.answer.citations.map((citation) => citation.quote).join(" ");
    assert.match(premises, /20|twenty/i); assert.match(premises, /30|thirty/i); assert.match(premises, /unopened/i);
    for (const citation of result.answer.citations) assert.ok(citation.excerpt.includes(citation.quote));
  });
  await check("Data reasoning explains a derived change with the quoted input rows", async () => {
    const result = await ask("How much did Orion revenue grow from April to May? Give the difference and percentage increase with the calculation.");
    assert.equal(result.answer.status, "answered"); assert.match(result.answer.text, /900/);
    assert.match(result.answer.text, /21\.(?:4|43)/);
    const quotes = result.answer.citations.map((citation) => citation.quote).join(" ");
    assert.match(quotes, /4200/); assert.match(quotes, /5100/);
    assert.match(result.answer.text, /Conclusion:/);
  });
  await check("Conditional reasoning respects a disqualifying exception", async () => {
    const result = await ask("If my order is 20 days old but I opened the package, should I expect a refund? Explain the relevant exception.");
    assert.equal(result.answer.status, "answered");
    assert.match(result.answer.text, /ineligible|not eligible|cannot|would not|do not|should not|disqualif/i);
    assert.ok(result.answer.citations.some((citation) => /Opened packages/.test(citation.quote)));
  });
  await check("A recommendation explains a documented next step for the eligible order", async () => {
    const result = await ask("Given order Luna's details and the refund policy, what should Luna do next to request the refund?");
    assert.equal(result.answer.status, "answered");
    assert.match(result.answer.text, /support/i); assert.match(result.answer.text, /order number/i);
    assert.match(result.answer.text, /Suggested next step:/);
    assert.ok(result.answer.citations.some((citation) => /contact support with the order number/.test(citation.quote)));
  });
  await check("An unrecorded condition stays unknown rather than becoming a negative fact", async () => {
    const result = await ask("Does order Luna have a purchase receipt?");
    assert.equal(result.answer.status, "insufficient_evidence");
    assert.deepEqual(result.answer.citations, []);
  });
  await check("Missing evidence abstains, and a foreign account has no source access", async () => {
    const result = await ask("What is the warranty duration for Orion?");
    assert.equal(result.answer.status, "insufficient_evidence"); assert.deepEqual(result.answer.citations, []);
    const other = await browser.newContext();
    await other.addCookies([{ name: "fixflow-test-identity", value: "user_preembedding_b", url: ui }]);
    assert.equal((await other.request.get(`${api}/sessions/${policy.sessionId}`)).status(), 404);
    const foreign = await other.request.post(`${api}/ask`, { data: { question: "refund deadline" }, headers: { "X-FixFlow-User-Id": "user_preembedding_a" } });
    assert.deepEqual((await foreign.json()).sources, []);
    await other.close();
  });
  await check("Mobile question/answer layout remains bounded", async () => {
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.screenshot({ path: resolve(output, `answer-${width}.png`), fullPage: true });
      const overflow = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        elements: [...document.querySelectorAll("body *")].filter((element) => {
          const box = element.getBoundingClientRect(); return box.width && box.right > innerWidth + 1;
        }).map((element) => ({ tag: element.tagName, className: element.className })).slice(0, 12),
      }));
      assert.ok(overflow.width <= width, `Overflow at ${width}px: ${JSON.stringify(overflow)}`);
    }
  });
  assert.deepEqual(report.errors, []);
  await context.tracing.stop({ path: resolve(output, "browser-trace.zip") });
} finally {
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2));
  if (browser) await browser.close();
  for (const child of [frontend, backend]) if (child && child.exitCode === null) { child.kill("SIGTERM"); await child.result; }
  for (let i = 0; i < commands.length; i++) await writeFile(resolve(output, `command-${i}.log`), (await commands[i].result).log);
}
