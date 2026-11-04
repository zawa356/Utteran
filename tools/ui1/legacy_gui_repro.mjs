// UI-1 reproduction harness for the legacy GUI (src/utteran_gui/web).
//
// Loads the REAL index.html / i18n.js / app.js / styles.css into Chromium with the
// same Content-Security-Policy the FastAPI app sends, and replaces only /api/* with a
// deterministic in-process mock. Each scenario drives the UI through real clicks or
// <select> changes, controls the completion order of the mocked responses, and reads
// the resulting DOM. No real backend, model, GPU, audio file or network is used; all
// values are synthetic.
//
// This is a Chromium (headless) result. It is NOT a Windows WebView2 / pywebview
// acceptance result.
//
// Usage (Playwright must be resolvable by Node; see docs/architecture/ui1/validation.md):
//   node tools/ui1/legacy_gui_repro.mjs            # all scenarios, human readable
//   node tools/ui1/legacy_gui_repro.mjs --json     # machine readable
//   node tools/ui1/legacy_gui_repro.mjs --only=env-stale-profile
//   node tools/ui1/legacy_gui_repro.mjs --expect-fixed   # exit 1 while any defect reproduces
//
// Exit codes: 0 = harness ran (defects may reproduce, see output), 1 = --expect-fixed and a
// defect reproduced, 2 = harness error.

import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    // Fall back to a globally installed package (e.g. the preinstalled cloud image).
    const { execSync } = require("node:child_process");
    const root = execSync("npm root -g", { encoding: "utf8" }).trim();
    return require(path.join(root, "playwright"));
  }
}
const { chromium } = loadPlaywright();

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(HERE, "../../src/utteran_gui/web");
const ORIGIN = "http://127.0.0.1:8765";
// Copied from src/utteran_gui/api.py secure_api(); keep in sync when it changes.
const CSP =
  "default-src 'self'; style-src 'self'; script-src 'self'; " +
  "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'";

const args = new Set(process.argv.slice(2));
const only = [...args].find((item) => item.startsWith("--only="))?.slice("--only=".length);

// ---------------------------------------------------------------------------
// Synthetic fixtures (shapes follow the fields app.js reads).

function environmentFor(profile) {
  return {
    active_profile: profile,
    profiles: [
      { name: "cpu", exists: true },
      { name: "cuda", exists: true },
    ],
    devices: {
      cpu: { logical_cores: 8 },
      ctranslate2: {
        cuda_devices: profile === "cuda" ? [{ name: "Synthetic GPU", usable: true }] : [],
      },
      pytorch: { xpu_devices: [] },
      native: { variants: {} },
    },
    models: [{ installed: true }],
    model_path: "models",
    openvino_models: [],
    errors: [],
    profile_warnings: [],
    options: {
      asr: [
        {
          id: "faster-whisper",
          label: "faster-whisper",
          models: [{ id: "small", label: "small" }],
          devices: [{ id: profile === "cuda" ? "cuda" : "cpu", label: profile, recommended: true }],
        },
      ],
      diarization: [],
      languages: ["ja", "en"],
      formats: ["srt", "json"],
      defaults: { asr_backend: "faster-whisper", asr_model: "small", asr_device: profile },
      guidance: [],
    },
  };
}

const SETTINGS = {
  theme: "light",
  language: "ja",
  default_profile: "cpu",
  default_input_dir: "",
  default_output_dir: "",
  log_dir: "logs",
  raw_subprocess_logs: false,
  token_configured: false,
  token_store_available: true,
  token_session_only: false,
  portable_distribution: false,
};

function jobSnapshot(id, status, logs = []) {
  return {
    id,
    status,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: status === "running" || status === "starting" ? null : "2026-01-01T00:00:01Z",
    exit_code: status === "completed" ? 0 : null,
    stalled: false,
    outputs: [],
    events: [],
    logs,
    guidance: null,
  };
}

// ---------------------------------------------------------------------------
// Mock server with controllable completion order.

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

class MockApi {
  constructor() {
    this.handlers = [];
    this.log = []; // { method, path, search, at }
    this.inflight = new Map(); // key -> count
    this.maxInflight = new Map();
  }

  on(method, pattern, handler) {
    this.handlers.unshift({ method, pattern, handler });
    return this;
  }

  count(method, pattern) {
    return this.log.filter(
      (item) => item.method === method && pattern.test(item.path + item.search),
    ).length;
  }

  async handle(route) {
    const request = route.request();
    const url = new URL(request.url());
    const entry = { method: request.method(), path: url.pathname, search: url.search, at: Date.now() };
    this.log.push(entry);
    const match = this.handlers.find(
      (item) => item.method === entry.method && item.pattern.test(entry.path + entry.search),
    );
    const key = `${entry.method} ${entry.path}`;
    this.inflight.set(key, (this.inflight.get(key) || 0) + 1);
    this.maxInflight.set(key, Math.max(this.maxInflight.get(key) || 0, this.inflight.get(key)));
    try {
      const result = match
        ? await match.handler({ url, request, entry })
        : { status: 404, body: { detail: `unmocked ${key}` } };
      if (result === "abort") {
        await route.abort("failed");
        return;
      }
      const headers = { "Cache-Control": "no-store", ...(result.headers || {}) };
      if (result.sse !== undefined) {
        await route.fulfill({
          status: 200,
          headers: { ...headers, "Content-Type": "text/event-stream" },
          body: result.sse,
        });
      } else {
        await route.fulfill({
          status: result.status ?? 200,
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify(result.body ?? {}),
        });
      }
    } finally {
      this.inflight.set(key, this.inflight.get(key) - 1);
    }
  }
}

function baseApi() {
  return new MockApi()
    .on("GET", /^\/api\/version$/, () => ({ body: { version: "0.0.0-ui1" } }))
    .on("GET", /^\/api\/settings$/, () => ({ body: { ...SETTINGS } }))
    .on("GET", /^\/api\/wizard\/status$/, () => ({ body: { first_run: false, step: "welcome" } }))
    .on("GET", /^\/api\/queue$/, () => ({ body: [] }))
    .on("GET", /^\/api\/token$/, () => ({ body: { configured: false, available: true } }))
    .on("GET", /^\/api\/environment/, ({ url }) => ({
      body: environmentFor(url.searchParams.get("profile") || "cpu"),
    }));
}

const STATIC = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/assets/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/assets/i18n.js": ["i18n.js", "text/javascript; charset=utf-8"],
  "/assets/styles.css": ["styles.css", "text/css; charset=utf-8"],
};

async function openPage(browser, api) {
  const context = await browser.newContext({ viewport: { width: 1180, height: 820 } });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__ui1 = { rejections: [], errors: [] };
    window.addEventListener("unhandledrejection", (event) => {
      window.__ui1.rejections.push(String(event.reason?.message || event.reason));
    });
    window.addEventListener("error", (event) => {
      window.__ui1.errors.push(String(event.message));
    });
  });
  await page.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/")) return api.handle(route);
    if (url.pathname === "/logo") return route.fulfill({ status: 204, body: "" });
    const entry = STATIC[url.pathname];
    if (!entry) return route.fulfill({ status: 404, body: "" });
    const body = await readFile(path.join(WEB_ROOT, entry[0]));
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": entry[1], "Content-Security-Policy": CSP },
      body,
    });
  });
  await page.goto(`${ORIGIN}/`);
  return { context, page };
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitFor(predicate, timeoutMs = 5000, label = "condition") {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    if (await predicate()) return;
    await sleep(25);
  }
  throw new Error(`timed out waiting for ${label}`);
}

const text = (page, selector) => page.locator(selector).textContent();
const visible = (page, selector) => page.locator(selector).isVisible();
const rejections = (page) => page.evaluate(() => window.__ui1.rejections.slice());

// Shared: fill the minimal job form and submit through the real button.
async function submitJob(page) {
  await page.fill("#input-path", "input/synthetic.wav");
  await page.fill("#output-dir", "output/synthetic");
  await page.click("#start-button");
}

// ---------------------------------------------------------------------------
// Scenarios. Each returns { expected, actual, reproduced, evidence }.

const scenarios = {
  // PR #23 re-verification on the current main, driven through the real <select>.
  "env-stale-profile": async (browser) => {
    const api = baseApi();
    const cudaGate = deferred();
    const cudaArrived = deferred();
    api.on("GET", /^\/api\/environment\?profile=cuda$/, async () => {
      cudaArrived.resolve();
      await cudaGate.promise; // simulates the uncached device probe of the newly selected profile
      return { body: environmentFor("cuda") };
    });
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(async () => (await text(page, "#profile-summary")) === "cpu", 5000, "boot");
      await page.selectOption("#profile-select", "cuda"); // request A (slow)
      await cudaArrived.promise;
      await page.selectOption("#profile-select", "cpu"); // request B (fast), user's final choice
      await waitFor(
        async () => api.count("GET", /^\/api\/environment\?profile=cpu$/) >= 2,
        5000,
        "second cpu request",
      );
      await sleep(200);
      const beforeRelease = await text(page, "#profile-summary");
      cudaGate.resolve(); // the older response completes last
      await sleep(400);
      const summary = await text(page, "#profile-summary");
      const selectValue = await page.locator("#profile-select").inputValue();
      return {
        expected: "profile-summary and #profile-select stay on the last user choice 'cpu'",
        actual: `before release='${beforeRelease}', after stale response: profile-summary='${summary}', select='${selectValue}'`,
        reproduced: summary === "cuda" || selectValue === "cuda",
        evidence: { requests: api.log.filter((item) => item.path === "/api/environment").map((item) => item.search) },
      };
    } finally {
      await context.close();
    }
  },

  // Wizard step handlers have no local catch: an API failure leaves the step unchanged
  // with no visible message (only an unhandledrejection, reported to the native log).
  "wizard-step-no-feedback": async (browser) => {
    const api = baseApi()
      .on("GET", /^\/api\/wizard\/status$/, () => ({ body: { first_run: true, step: "welcome" } }))
      .on("POST", /^\/api\/wizard\/start$/, () => ({ body: {} }))
      .on("GET", /^\/api\/wizard\/hardware$/, () => ({
        body: {
          recommendation: {
            recommended: "cpu",
            reasons: ["synthetic reason"],
            detection_confident: true,
            alternatives: [
              {
                profile: "cpu",
                asr_accelerated: false,
                diarization_accelerated: false,
                approx_disk_bytes: 1024,
                caveat: null,
                extra_setup: [],
              },
            ],
          },
        },
      }))
      .on("PUT", /^\/api\/wizard\/state$/, () => ({
        status: 500,
        body: { detail: "synthetic wizard state failure" },
      }));
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(() => visible(page, "#wizard-step-welcome"), 5000, "wizard welcome");
      await page.click("#wizard-begin");
      await waitFor(() => visible(page, "#wizard-step-profile"), 5000, "profile step");
      await page.click("#wizard-profile-next");
      await waitFor(() => api.count("PUT", /^\/api\/wizard\/state$/) >= 1, 5000, "PUT state");
      await sleep(600);
      const stillProfile = await visible(page, "#wizard-step-profile");
      const dialog = await visible(page, "#app-dialog");
      const bodyText = await page.locator("body").innerText();
      const shown = bodyText.includes("synthetic wizard state failure");
      const unhandled = await rejections(page);
      return {
        expected: "the failure is shown next to the button (or in a dialog) with a retry path",
        actual: `step unchanged=${stillProfile}, dialog=${dialog}, message visible=${shown}, unhandledrejection=${unhandled.length}`,
        reproduced: stillProfile && !dialog && !shown && unhandled.length > 0,
        evidence: { unhandled },
      };
    } finally {
      await context.close();
    }
  },

  // SSE `done` and the 1 s poller both reach finishJob(); there is no terminal guard, so the
  // result is rendered twice and the retained logs are appended twice.
  "job-finish-twice": async (browser) => {
    const api = baseApi();
    let gets = 0;
    const secondGet = deferred();
    api
      .on("POST", /^\/api\/jobs$/, () => ({ status: 202, body: jobSnapshot("job-a", "starting") }))
      .on("GET", /^\/api\/jobs\/job-a\/events$/, () => ({
        sse: 'retry: 3600000\nid: 0\nevent: done\ndata: {"event":"done"}\n\n',
      }))
      .on("GET", /^\/api\/jobs\/job-a$/, async () => {
        gets += 1;
        if (gets === 1) await secondGet.promise; // finishJob() from SSE is slow ...
        if (gets === 2) secondGet.resolve(); // ... until the poller's own GET arrives
        return { body: jobSnapshot("job-a", "completed", ["synthetic retained log line"]) };
      });
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(async () => (await text(page, "#form-status")) !== "", 5000, "environment");
      await submitJob(page);
      await waitFor(() => visible(page, "#result-panel"), 6000, "result panel");
      await sleep(800);
      const log = await text(page, "#log-output");
      const occurrences = log.split("synthetic retained log line").length - 1;
      return {
        expected: "one terminal transition: the retained log line appears once",
        actual: `log line appears ${occurrences} time(s); GET /api/jobs/job-a issued ${gets} time(s)`,
        reproduced: occurrences > 1,
        evidence: { gets, occurrences },
      };
    } finally {
      await context.close();
    }
  },

  // A finishJob() GET for job A that resolves after job B was submitted overwrites
  // state.job and closes B's EventSource and poller: B is no longer tracked.
  "stale-finish-orphans-next-job": async (browser) => {
    const api = baseApi();
    const finishGate = deferred();
    const finishArrived = deferred();
    let jobPosts = 0;
    api
      .on("POST", /^\/api\/jobs$/, () => {
        jobPosts += 1;
        return { status: 202, body: jobSnapshot(jobPosts === 1 ? "job-a" : "job-b", "starting") };
      })
      .on("GET", /^\/api\/jobs\/job-a\/events$/, () => ({
        sse: 'retry: 3600000\nid: 0\nevent: done\ndata: {"event":"done"}\n\n',
      }))
      .on("GET", /^\/api\/jobs\/job-a$/, async () => {
        finishArrived.resolve();
        await finishGate.promise;
        return { body: jobSnapshot("job-a", "completed", []) };
      })
      .on("GET", /^\/api\/jobs\/job-b\/events$/, () => ({ sse: "retry: 3600000\n\n" }))
      .on("GET", /^\/api\/jobs\/job-b$/, () => ({ body: jobSnapshot("job-b", "running") }));
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(async () => (await text(page, "#form-status")) !== "", 5000, "environment");
      await submitJob(page); // job A
      await finishArrived.promise; // A's finishJob() GET is in flight
      await waitFor(async () => !(await page.locator("#start-button").isDisabled()), 3000, "start re-enabled");
      await submitJob(page); // job B
      await waitFor(() => api.count("GET", /^\/api\/jobs\/job-b$/) >= 1, 4000, "job B polled");
      finishGate.resolve(); // stale completion of A
      await sleep(300);
      const pollsAtRelease = api.count("GET", /^\/api\/jobs\/job-b$/);
      await sleep(3200);
      const pollsAfter = api.count("GET", /^\/api\/jobs\/job-b$/) - pollsAtRelease;
      const resultShown = await visible(page, "#result-panel");
      return {
        expected: "job B keeps being tracked (~3 polls in 3.2 s) and A's late result does not replace B's view",
        actual: `job B polls after A's stale completion: ${pollsAfter}; result panel visible=${resultShown}`,
        reproduced: pollsAfter === 0,
        evidence: { pollsAtRelease, pollsAfter },
      };
    } finally {
      await context.close();
    }
  },

  // Settings save has no local catch: a failed PATCH shows nothing.
  "settings-save-no-feedback": async (browser) => {
    const api = baseApi().on("PATCH", /^\/api\/settings$/, () => ({
      status: 500,
      body: { detail: "synthetic settings failure" },
    }));
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(async () => (await text(page, "#form-status")) !== "", 5000, "environment");
      await page.click('.nav-item[data-view="settings"]');
      await page.click('#settings-form button[type="submit"]');
      await waitFor(() => api.count("PATCH", /^\/api\/settings$/) >= 1, 4000, "PATCH");
      await sleep(500);
      const status = (await text(page, "#settings-status")) || "";
      const dialog = await visible(page, "#app-dialog");
      const unhandled = await rejections(page);
      return {
        expected: "the save failure is shown in #settings-status or a dialog",
        actual: `settings-status='${status}', dialog=${dialog}, unhandledrejection=${unhandled.length}`,
        reproduced: status === "" && !dialog && unhandled.length > 0,
        evidence: { unhandled },
      };
    } finally {
      await context.close();
    }
  },

  // Cancel: button disabled + "cancellation pending" before the POST; a failed POST is not
  // reported and the button never re-enables.
  "cancel-failure-stuck": async (browser) => {
    const api = baseApi()
      .on("POST", /^\/api\/jobs$/, () => ({ status: 202, body: jobSnapshot("job-c", "starting") }))
      .on("GET", /^\/api\/jobs\/job-c\/events$/, () => ({ sse: "retry: 3600000\n\n" }))
      .on("GET", /^\/api\/jobs\/job-c$/, () => ({ body: jobSnapshot("job-c", "running") }))
      .on("POST", /^\/api\/jobs\/job-c\/cancel$/, () => ({
        status: 500,
        body: { detail: "synthetic cancel failure" },
      }));
    const { context, page } = await openPage(browser, api);
    try {
      await waitFor(async () => (await text(page, "#form-status")) !== "", 5000, "environment");
      await submitJob(page);
      await waitFor(() => visible(page, "#progress-panel"), 4000, "progress panel");
      await waitFor(async () => !(await page.locator("#start-button").isDisabled()), 3000, "accepted");
      await page.click("#cancel-button");
      await waitFor(() => api.count("POST", /^\/api\/jobs\/job-c\/cancel$/) >= 1, 4000, "cancel POST");
      await sleep(1500);
      const disabled = await page.locator("#cancel-button").isDisabled();
      const warning = (await text(page, "#stall-warning")) || "";
      const shown = (await page.locator("body").innerText()).includes("synthetic cancel failure");
      const unhandled = await rejections(page);
      return {
        expected: "the cancel failure is shown and the cancel button becomes usable again",
        actual: `cancel disabled=${disabled}, pending text='${warning.trim()}', failure visible=${shown}, unhandledrejection=${unhandled.length}`,
        reproduced: disabled && !shown,
        evidence: { unhandled },
      };
    } finally {
      await context.close();
    }
  },

  // setInterval(loadQueue, 1000) does not wait for the previous request: slow responses pile up,
  // and failures are swallowed by `.catch(() => {})`.
  "queue-poll-overlap": async (browser) => {
    const api = baseApi();
    let calls = 0;
    api.on("GET", /^\/api\/queue$/, async () => {
      calls += 1;
      if (calls === 1) return { body: [] }; // boot call
      await sleep(3500); // slow backend
      return { status: 503, body: { detail: "synthetic queue failure" } };
    });
    const { context, page } = await openPage(browser, api);
    try {
      await sleep(6000);
      const maxConcurrent = api.maxInflight.get("GET /api/queue") || 0;
      const shown = (await page.locator("body").innerText()).includes("synthetic queue failure");
      return {
        expected: "at most one queue request in flight; a failing queue is visible to the user",
        actual: `max concurrent GET /api/queue=${maxConcurrent}; failure visible=${shown}; total calls=${calls}`,
        reproduced: maxConcurrent > 1 && !shown,
        evidence: { maxConcurrent, calls },
      };
    } finally {
      await context.close();
    }
  },
};

// ---------------------------------------------------------------------------

async function main() {
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const [name, run] of Object.entries(scenarios)) {
      if (only && name !== only) continue;
      const started = Date.now();
      try {
        const result = await run(browser);
        results.push({ name, ...result, ms: Date.now() - started });
      } catch (error) {
        results.push({ name, error: String(error?.stack || error), ms: Date.now() - started });
      }
    }
  } finally {
    await browser.close();
  }
  const meta = { browser: `chromium ${browser.version()}`, node: process.version, platform: process.platform };
  if (args.has("--json")) {
    console.log(JSON.stringify({ meta, results }, null, 2));
  } else {
    console.log(`UI-1 legacy GUI repro — ${meta.browser}, node ${meta.node}, ${meta.platform}`);
    for (const item of results) {
      if (item.error) {
        console.log(`\n[ERROR] ${item.name}\n  ${item.error}`);
        continue;
      }
      console.log(`\n[${item.reproduced ? "REPRODUCED" : "not reproduced"}] ${item.name} (${item.ms} ms)`);
      console.log(`  expected: ${item.expected}`);
      console.log(`  actual:   ${item.actual}`);
    }
  }
  if (results.some((item) => item.error)) process.exit(2);
  if (args.has("--expect-fixed") && results.some((item) => item.reproduced)) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  process.exit(2);
});
