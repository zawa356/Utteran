// Same-condition Playwright run for the three UI-1 spikes.
// Usage: node e2e/run.mjs [--shots] [--out=report.json]   (spike root; expects <app>/dist built)
// Exit codes: 0 = every scenario ok and 0 CSP violations under the nonce policy for every app,
//             1 = at least one scenario failed or a nonce-policy violation occurred, 2 = harness error.
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require("playwright"); // e2e/node_modules (pinned in e2e/package.json)
  } catch {
    return require(path.join(execSync("npm root -g", { encoding: "utf8" }).trim(), "playwright"));
  }
}
const { chromium } = loadPlaywright();
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const APPS = ["vue-primevue", "vue-vuetify", "react-mui"];
const NONCE = "ui1spikenonce0123456789";
// Current production CSP (src/utteran_gui/api.py) and the nonce variant UI-2 would need.
const CSP_STRICT = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'";
const CSP_NONCE = CSP_STRICT.replace("style-src 'self'", `style-src 'self' 'nonce-${NONCE}'`);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".svg": "image/svg+xml" };

function serve(app, csp) {
  const dist = path.join(ROOT, app, "dist");
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    const file = path.join(dist, url.pathname === "/" ? "index.html" : url.pathname);
    if (!file.startsWith(dist)) return res.writeHead(403).end();
    try {
      let body = await readFile(file);
      if (file.endsWith("index.html")) body = Buffer.from(body.toString().replace("__CSP_NONCE__", csp === CSP_NONCE ? NONCE : "__none__"));
      res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream", "Content-Security-Policy": csp });
      res.end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function open(browser, server, viewport = { width: 1440, height: 900 }, deviceScaleFactor = 1) {
  const context = await browser.newContext({ viewport, deviceScaleFactor });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener("securitypolicyviolation", (e) => window.__violations.push(`${e.effectiveDirective}`));
  });
  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForSelector('[data-testid="ready"]');
  await page.waitForFunction(() => window.__spike?.state().environment.phase === "success");
  return { context, page, readyMs: Date.now() - t0 };
}

// Kit-neutral drivers: every kit exposes role=combobox / role=option and a checkbox input.
async function pick(page, testid, value) {
  const root = page.locator(`[data-testid="${testid}"]`).first();
  let target = root.locator('[role="combobox"]').first();
  if (!(await target.count())) {
    const sibling = root.locator("xpath=..").locator('[role="combobox"]').first();
    target = (await sibling.count()) ? sibling : root;
  }
  await target.click();
  await page.getByRole("option", { name: value, exact: true }).locator("visible=true").last().click();
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(150);
}
async function toggle(page, testid) {
  const root = page.locator(`[data-testid="${testid}"]`).first();
  const input = root.locator('input[type="checkbox"]').first();
  if (await input.count()) await input.click({ force: true });
  else await root.click();
}
const tid = (page, id) => page.locator(`[data-testid="${id}"]`).first();
const txt = async (page, id) => ((await tid(page, id).textContent()) || "").trim();
const state = (page) => page.evaluate(() => window.__spike.state());
function check(results, name, ok, detail) {
  results.push({ name, ok: Boolean(ok), detail });
}

async function scenarios(page) {
  const r = [];
  // 1. shared default + per-file override
  await tid(page, "file-row-f2").click();
  await toggle(page, "override-language");
  await pick(page, "inspector-language", "en");
  await pick(page, "default-language", "auto");
  check(r, "override", (await txt(page, "effective-language-f1")) === "auto" && (await txt(page, "effective-language-f2")) === "en",
    `f1=${await txt(page, "effective-language-f1")} f2=${await txt(page, "effective-language-f2")}`);
  // 2. stale environment response (slow cuda, then fast cpu)
  await tid(page, "profile-cuda").click();
  await tid(page, "profile-cpu").click();
  await page.waitForTimeout(700);
  check(r, "latest-wins", (await txt(page, "profile-current")) === "cpu", `profile=${await txt(page, "profile-current")}`);
  // 3. 409 + double click + retry with the same Idempotency-Key
  await page.evaluate(() => window.__spike.setFault("conflict"));
  await tid(page, "submit").dblclick();
  await tid(page, "submit-error").waitFor();
  const key1 = (await state(page)).submit.idempotencyKey;
  const calls409 = await page.evaluate(() => window.__spike.submitCalls());
  await page.evaluate(() => window.__spike.setFault("none"));
  await tid(page, "retry").click();
  await tid(page, "job-row-job-1").waitFor();
  const s3 = await state(page);
  check(r, "conflict-retry", calls409 === 1 && s3.submit.idempotencyKey === key1 && Object.keys(s3.jobs).length === 1,
    `posts on dblclick=${calls409}, key reused=${s3.submit.idempotencyKey === key1}, jobs=${Object.keys(s3.jobs).length}`);
  // 4. timeout is visible and retryable
  await page.evaluate(() => window.__spike.setFault("timeout"));
  await tid(page, "submit").click();
  const pendingSeen = (await txt(page, "submit-status")) === "pending";
  await tid(page, "submit-error").waitFor({ timeout: 4000 });
  const timeoutText = await txt(page, "submit-error");
  await page.evaluate(() => window.__spike.setFault("none"));
  await tid(page, "retry").click();
  await tid(page, "job-row-job-2").waitFor();
  check(r, "timeout-retry", pendingSeen && timeoutText.includes("timeout"), `pending=${pendingSeen} error='${timeoutText.slice(0, 40)}'`);
  // 5. epoch:seq reducer: new, duplicate, older, resync_required
  await page.evaluate(async () => {
    const { controller } = window.__spike;
    const b = controller.backend;
    const running = b.advance("job-1", "running");
    const done = b.advance("job-1", "succeeded");
    await controller.receive(running);
    await controller.receive(done);
    await controller.receive(done); // duplicate
    await controller.receive(running); // out of order
    await controller.receive({ id: "e1:999", type: "resync_required" });
  });
  await page.waitForTimeout(100);
  const s5 = await state(page);
  check(r, "event-reducer", (await txt(page, "job-status-job-1")) === "succeeded" && s5.stats.ignored === 2 && s5.stats.resyncs >= 3,
    `status=${await txt(page, "job-status-job-1")} ${await txt(page, "event-stats")}`);
  // 6. Normal <-> Expert keeps settings and jobs
  await tid(page, "mode-normal").click();
  const normalVisible = await tid(page, "normal-start").isVisible();
  await tid(page, "mode-expert").click();
  check(r, "mode-switch-keeps-state", normalVisible && (await txt(page, "effective-language-f2")) === "en" && (await tid(page, "job-row-job-2").count()) === 1,
    `normal visible=${normalVisible}, f2=${await txt(page, "effective-language-f2")}`);
  // 7. keyboard: Tab reaches the submit button and Enter submits
  await tid(page, "submit").focus();
  await page.keyboard.press("Enter");
  await tid(page, "job-row-job-3").waitFor({ timeout: 3000 }).catch(() => {});
  check(r, "keyboard-submit", (await tid(page, "job-row-job-3").count()) === 1, "Enter on focused submit");
  return r;
}

async function heapMb(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  await cdp.send("HeapProfiler.collectGarbage");
  const { metrics } = await cdp.send("Performance.getMetrics");
  return (metrics.find((m) => m.name === "JSHeapUsedSize").value / 1048576).toFixed(1);
}

async function main() {
  const shots = process.argv.includes("--shots");
  const browser = await chromium.launch();
  const report = { browser: `chromium ${browser.version()}`, apps: {} };
  for (const app of APPS) {
    const out = (report.apps[app] = {});
    // strict = today's CSP without changes
    const strict = await serve(app, CSP_STRICT);
    {
      const { context, page } = await open(browser, strict);
      await page.waitForTimeout(300);
      out.strictCspViolations = await page.evaluate(() => window.__violations.length);
      out.strictCspStyledButton = await page.evaluate(() => {
        const el = document.querySelector('[data-testid="submit"]');
        return el ? getComputedStyle(el).backgroundColor : "none";
      });
      await context.close();
    }
    strict.close();
    const nonce = await serve(app, CSP_NONCE);
    const ready = [];
    for (let i = 0; i < 5; i += 1) {
      const { context, readyMs } = await open(browser, nonce);
      ready.push(readyMs);
      await context.close();
    }
    out.readyMsMedian = ready.sort((a, b) => a - b)[2];
    const { context, page } = await open(browser, nonce);
    out.heapAfterLoadMb = await heapMb(page);
    page.setDefaultTimeout(5000);
    try {
      out.scenarios = await scenarios(page);
    } catch (error) {
      // A scenario that throws (e.g. a missing element) is a failed scenario, not a harness error.
      out.scenarios = [{ name: "scenario-exception", ok: false, detail: String(error?.message || error).split("\n")[0] }];
    }
    out.heapAfterScenariosMb = await heapMb(page);
    out.nonceCspViolations = await page.evaluate(() => window.__violations.length);
    await context.close();
    if (shots) {
      for (const [w, h, dsf] of [[1440, 900, 1], [1024, 700, 2]]) {
        const s = await open(browser, nonce, { width: w, height: h }, dsf);
        await s.page.screenshot({ path: path.join(ROOT, `shot-${app}-${w}x${h}@${dsf}.png`) });
        await s.context.close();
      }
    }
    nonce.close();
  }
  await browser.close();
  const failures = [];
  for (const [app, out] of Object.entries(report.apps)) {
    for (const item of out.scenarios) if (!item.ok) failures.push(`${app}: scenario ${item.name} failed (${item.detail})`);
    if (out.scenarios.length !== 7) failures.push(`${app}: expected 7 scenarios, ran ${out.scenarios.length}`);
    if (out.nonceCspViolations !== 0) failures.push(`${app}: ${out.nonceCspViolations} CSP violation(s) under the nonce policy`);
  }
  report.failures = failures;
  const outArg = process.argv.find((item) => item.startsWith("--out="));
  if (outArg) await writeFile(outArg.slice("--out=".length), JSON.stringify(report, null, 2));
  else console.log(JSON.stringify(report, null, 2));
  for (const line of failures) console.error(`FAIL ${line}`);
  console.error(failures.length ? `${failures.length} failure(s)` : "all apps: 7/7 scenarios ok, 0 nonce-policy CSP violations");
  process.exit(failures.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
