import { spawn } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";
import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, openSingleJobTools, SESSION, SESSION_HEADER } from "./fixtures";
import { test } from "./isolatedV1";

/**
 * Bundle E-C against the real FastAPI app in the `--next-ui-v1` shape: a finished synthetic job whose
 * JSON (written by the shared renderer, tools/ui2/synthetic_v1.py) is re-exported without ASR into a
 * new folder. The test reads the synthetic output folder directly to prove what was (not) written.
 * Headless Chromium on Linux — NOT a WebView2 acceptance (#22), NOT evidence for Windows (refused there).
 */

const PRESET = "E-C 合成";
const POLICY = "E-C CPU";
const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    const { pathname } = new URL(r.url());
    if (pathname.startsWith("/api/") && !pathname.startsWith("/api/v1/worksets")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;

async function library(page: Page, request: APIRequestContext, base = ""): Promise<void> {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  const presets = (await (await request.get(`${base}/api/v1/presets`, { headers: AUTH })).json()) as { items: { name: string }[] };
  if (!presets.items.some((p) => p.name === PRESET)) {
    await page.getByTestId("v1-preset-create-open").click();
    await page.getByTestId("v1-preset-name").locator("input").fill(PRESET);
    await page.getByTestId("v1-preset-model").locator("input").fill("large-v3-turbo");
    // The new-preset default selects JSON; unchecking it shows the re-export warning.
    await expect(page.getByTestId("v1-preset-format-json").locator("input")).toBeChecked();
    await expect(page.getByTestId("v1-preset-no-json")).toHaveCount(0);
    await page.getByTestId("v1-preset-format-json").locator("input").uncheck();
    await expect(page.getByTestId("v1-preset-no-json")).toBeVisible();
    await page.getByTestId("v1-preset-format-json").locator("input").check();
    await expect(page.getByTestId("v1-preset-no-json")).toHaveCount(0);
    await page.getByTestId("v1-preset-check").click();
    await page.getByTestId("v1-preset-save").click();
  } else {
    await choose(page, "v1-preset-select", new RegExp(PRESET));
  }
  const policies = (await (await request.get(`${base}/api/v1/execution-policies`, { headers: AUTH })).json()) as { items: { name: string }[] };
  if (!policies.items.some((p) => p.name === POLICY)) {
    await page.getByTestId("v1-policy-create-open").click();
    await page.getByTestId("v1-policy-name").locator("input").fill(POLICY);
    await page.getByTestId("v1-policy-check").click();
    await page.getByTestId("v1-policy-save").click();
  } else {
    await choose(page, "v1-policy-select", new RegExp(POLICY));
  }
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(PRESET);
}

async function finishedJob(page: Page, request: APIRequestContext, seen: Request[], root: string, name: string, base = ""): Promise<string> {
  await page.getByTestId("mode-expert").click();
  await page.getByTestId("nav-transcribe").click();
  await library(page, request, base);
  await page.getByTestId("v1-output-path").locator("input").fill(join(root, "out"));
  const none = page.getByTestId("tx-list-select-none");
  if ((await none.count()) > 0 && (await none.isEnabled())) await none.click();
  await page.getByTestId("tx-input-path").locator("input").fill(join(root, "in", name));
  await page.getByTestId("tx-input-add-typed").click();
  await page.getByTestId("tx-list-check").last().locator("input").check();
  await page.getByTestId("v1-batch-check").click();
  await expect(page.getByTestId("v1-batch-resolved")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("v1-batch-submit").click();
  await expect(page.getByTestId("v1-batch-accepted")).toBeVisible({ timeout: 20_000 });
  const post = seen.filter((r) => line(r) === "POST /api/v1/jobs").at(-1)!;
  const [id] = ((await (await post.response())!.json()) as { jobs: { id: string }[] }).jobs.map((j) => j.id);
  await openSingleJobTools(page);
  const dispatch = page.getByTestId("v1-queue-dispatch");
  if (/停止中/.test((await dispatch.textContent()) ?? "")) await page.getByTestId("v1-queue-resume").click();
  await page.getByTestId("nav-results").click();
  await page.getByTestId("results-tab-history").click();
  await expect(row(page, id!)).toHaveAttribute("data-status", "succeeded", { timeout: 30_000 });
  return id!;
}

const row = (page: Page, id: string) => page.getByTestId("v1-board-history").locator(`[data-testid="v1-board-row"][data-root="${id}"]`);
const dialog = (page: Page) => page.getByTestId("reexport-dialog");

/** Name -> bytes (hex) of every entry of a directory, without following links. */
function snapshot(directory: string): Record<string, string> {
  return Object.fromEntries(readdirSync(directory).sort().map((name) => [name, readFileSync(join(directory, name)).toString("hex")]));
}
function ownDirectory(out: string, jobId: string): string {
  for (const name of readdirSync(out)) {
    const marker = join(out, name, ".utteran-job");
    if (existsSync(marker) && readFileSync(marker, "utf8").trim() === jobId) return join(out, name);
  }
  throw new Error("job directory not found");
}

test("re-export: keyboard, frozen defaults, relabel, CRLF, a new folder; the job and its files are unchanged", async ({ app, backend }) => {
  test.setTimeout(120_000);
  const seen = recordApi(app);
  const id = await finishedJob(app, app.request, seen, backend.root, "reexport-meeting.wav");
  const out = join(backend.root, "out");
  const original = ownDirectory(out, id);
  const before = snapshot(original);
  const jobsBefore = seen.filter((r) => line(r) === "POST /api/v1/jobs").length;

  await row(app, id).getByTestId("results-select").click();
  // Distinct from the D3-B2 rerun button.
  await expect(app.getByTestId("result-rerun")).toBeVisible();
  const opener = app.getByTestId("result-reexport");
  await opener.focus();
  await app.keyboard.press("Enter");
  await expect(dialog(app)).toHaveAttribute("data-step", "form");
  await expect(app.getByTestId("reexport-close")).toBeFocused();
  // Frozen output settings of the job are the initial values.
  await expect(app.getByTestId("reexport-format-srt").locator("input")).toBeChecked();
  await expect(app.getByTestId("reexport-format-json").locator("input")).toBeChecked();
  await expect(app.getByTestId("reexport-format-md").locator("input")).toBeChecked();
  await expect(app.getByTestId("reexport-format-txt").locator("input")).not.toBeChecked();
  await expect(app.getByTestId("reexport-source-summary")).toContainText("3");
  // Cancel sends nothing and returns focus to the opener.
  await app.getByTestId("reexport-close").click();
  await expect(dialog(app)).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(seen.filter((r) => line(r).endsWith("/reexports") && r.method() === "POST")).toHaveLength(0);

  await opener.click();
  await expect(dialog(app)).toHaveAttribute("data-step", "form");
  await app.getByTestId("reexport-format-txt").locator("input").check();
  await app.getByTestId("reexport-format-vtt").locator("input").check();
  await app.getByTestId("reexport-format-srt").locator("input").uncheck();
  await expect(app.getByTestId("reexport-srt-bom").locator("input")).toBeDisabled();
  await app.getByTestId("reexport-newline-crlf").locator("input").check();
  await app.getByTestId("reexport-label-0").locator("input").fill("Alice");
  await app.getByTestId("reexport-submit").click();
  await expect(app.getByTestId("reexport-result")).toHaveAttribute("data-state", "completed", { timeout: 20_000 });
  const sent = seen.filter((r) => r.method() === "POST" && line(r).endsWith("/reexports"));
  expect(sent).toHaveLength(1);
  const headers = await sent[0]!.allHeaders();
  expect(headers["if-match"]).toMatch(/^"\d+"$/);
  expect(headers["idempotency-key"]).toBeTruthy();
  expect(new URL(sent[0]!.url()).pathname).not.toContain("Alice");

  // A new folder next to the job's own one, with exactly the chosen formats.
  const created = readdirSync(out).filter((name) => name.startsWith("reexport-meeting_reexport_"));
  expect(created).toHaveLength(1);
  const target = join(out, created[0]!);
  expect(readdirSync(target).sort()).toEqual([".utteran-reexport", `${created[0]}.json`, `${created[0]}.md`, `${created[0]}.txt`, `${created[0]}.vtt`].sort());
  const txt = readFileSync(join(target, `${created[0]}.txt`), "utf8");
  expect(txt).toBe("Alice: 合成の発話です。\r\nSPEAKER_01: second line\r\nno speaker\r\n");
  const json = JSON.parse(readFileSync(join(target, `${created[0]}.json`), "utf8")) as { speakers: string[] };
  expect(json.speakers).toEqual(["Alice", "SPEAKER_01"]);
  // The job's own folder, marker and files are byte-identical; no job was created or re-run.
  expect(snapshot(original)).toEqual(before);
  expect(seen.filter((r) => line(r) === "POST /api/v1/jobs").length).toBe(jobsBefore);
  expect(seen.some((r) => /\/(rerun|retry|execution-resolutions)$/.test(new URL(r.url()).pathname) && sent[0]!.timing().startTime <= r.timing().startTime)).toBe(false);
  await expect(app.getByTestId("reexport-directory")).toContainText(created[0]!);

  // "Re-export with other settings" is a new request (new key, new folder), not a replay.
  await app.getByTestId("reexport-again").click();
  await app.getByTestId("reexport-submit").click();
  await expect(app.getByTestId("reexport-result")).toHaveAttribute("data-state", "completed", { timeout: 20_000 });
  const both = seen.filter((r) => r.method() === "POST" && line(r).endsWith("/reexports"));
  expect(both).toHaveLength(2);
  expect((await both[1]!.allHeaders())["idempotency-key"]).not.toBe(headers["idempotency-key"]);
  expect(readdirSync(out).filter((name) => name.startsWith("reexport-meeting_reexport_"))).toHaveLength(2);
  await expect(app.getByTestId("reexport-history")).toContainText("2");
  await app.getByTestId("reexport-close").click();
  await expect(opener).toBeFocused();
});

test("refusals: JSON not written, changed JSON; the reason is shown and nothing is sent", async ({ app, backend }) => {
  test.setTimeout(120_000);
  const seen = recordApi(app);
  const plain = await finishedJob(app, app.request, seen, backend.root, "meeting.wav");
  await row(app, plain).getByTestId("results-select").click();
  await app.getByTestId("result-reexport").click();
  await expect(app.getByTestId("reexport-refusal")).toHaveAttribute("data-refusal", "source_json_not_recorded");
  await expect(app.getByTestId("reexport-refusal")).toContainText("JSONを出力していません");
  await expect(app.getByTestId("reexport-submit")).toHaveCount(0);
  await app.keyboard.press("Escape");
  await expect(dialog(app)).toHaveCount(0);

  const json = await finishedJob(app, app.request, seen, backend.root, "reexport-meeting.wav");
  const directory = ownDirectory(join(backend.root, "out"), json);
  const file = join(directory, `${directory.split("/").at(-1)}.json`);
  const { writeFileSync } = await import("node:fs");
  writeFileSync(file, readFileSync(file, "utf8").replace("合成", "改変"));
  await row(app, json).getByTestId("results-select").click();
  await app.getByTestId("result-reexport").click();
  await expect(app.getByTestId("reexport-refusal")).toHaveAttribute("data-refusal", "source_changed");
  const text = (await dialog(app).textContent()) ?? "";
  expect(text).not.toContain(backend.root);
  await app.getByTestId("reexport-close").click();
  expect(seen.filter((r) => r.method() === "POST" && line(r).endsWith("/reexports"))).toHaveLength(0);
  const storage = await app.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }));
  expect(storage).not.toContain(backend.root);
});

test("English text and a lost response: the dialog stays, recheck replays the same key once", async ({ app, backend }) => {
  test.setTimeout(120_000);
  const seen = recordApi(app);
  const id = await finishedJob(app, app.request, seen, backend.root, "reexport-meeting.wav");
  await app.request.patch("/api/settings", { headers: SESSION_HEADER, data: { language: "en" } });
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await app.getByTestId("mode-expert").click(); // results and history are an Expert page
  await app.getByTestId("nav-results").click();
  await app.getByTestId("results-tab-history").click();
  await row(app, id).getByTestId("results-select").click();
  await app.getByTestId("result-reexport").click();
  await expect(dialog(app)).toContainText("Re-export from the verified JSON");
  let dropped = false;
  await app.route("**/api/v1/jobs/*/reexports", async (route) => {
    if (route.request().method() === "POST" && !dropped) {
      dropped = true;
      await route.fetch(); // the server applies the request ...
      await route.abort("connectionreset"); // ... and the answer is lost
      return;
    }
    await route.fallback();
  });
  await app.getByTestId("reexport-submit").click();
  await expect(app.getByTestId("reexport-unknown")).toBeVisible();
  await app.keyboard.press("Escape");
  await expect(dialog(app)).toBeVisible(); // persistent while the outcome is unknown
  await expect(app.getByTestId("reexport-close")).toBeDisabled();
  await app.getByTestId("reexport-recheck").click();
  await expect(app.getByTestId("reexport-result")).toHaveAttribute("data-state", "completed", { timeout: 20_000 });
  const posts = seen.filter((r) => r.method() === "POST" && line(r).endsWith("/reexports"));
  expect(posts).toHaveLength(2);
  expect((await posts[0]!.allHeaders())["idempotency-key"]).toBe((await posts[1]!.allHeaders())["idempotency-key"]);
  expect(posts[0]!.postData()).toBe(posts[1]!.postData());
  // One folder only: the recheck answered from the record of the first request.
  expect(readdirSync(join(backend.root, "out")).filter((name) => name.includes("_reexport_"))).toHaveLength(1);
  await app.request.patch("/api/settings", { headers: SESSION_HEADER, data: { language: "ja" } });
});

// ---- the record after an application restart (same data root) ----------------------------------

async function serve(root: string, directory: string, reuse: boolean) {
  const args = ["run", "--no-sync", "python", "../tools/ui2/serve_next_ui.py", "--port", "0", "--v1", "--data-root", root, "--exit-on-stdin-close"];
  if (reuse) args.push("--reuse-data-root");
  const child = spawn("uv", args, { env: { ...process.env, TMPDIR: directory, TEMP: directory, TMP: directory }, stdio: ["pipe", "pipe", "inherit"] });
  const closed = new Promise<number | null>((resolve) => child.once("close", resolve));
  const lines = createInterface({ input: child.stdout });
  const url = await new Promise<string>((resolve, reject) => {
    // Bounded: a server that prints nothing fails the test instead of hanging it.
    const timer = setTimeout(() => reject(new Error("server did not start")), 30_000);
    child.once("exit", (code) => reject(new Error(`server exited (${code})`)));
    lines.on("line", (text) => {
      const match = /^http:\/\/127\.0\.0\.1:\d+(?=\/launch\?)/.exec(text);
      if (match) {
        clearTimeout(timer);
        resolve(match[0]);
      }
    });
  });
  return {
    url,
    async stop() {
      child.stdin.end();
      const exit = await Promise.race([closed, delay(10_000, "timeout" as const)]);
      lines.close();
      expect(exit, "restart harness shutdown").toBe(0);
    },
  };
}

test.describe("restart", () => {
  test("the re-export record is shown after the application restarts", async ({ browser }) => {
    test.setTimeout(180_000);
    const directory = await mkdtemp(join(tmpdir(), "utteran-ec-e2e-"));
    const root = join(directory, "v1");
    try {
      const first = await serve(root, directory, false);
      let page = await browser.newPage();
      const seen = recordApi(page);
      await page.goto(`${first.url}/launch?session=${SESSION}`);
      await expect(page.getByTestId("app-ready")).toBeVisible();
      const id = await finishedJob(page, page.request, seen, root, "reexport-meeting.wav", first.url);
      await row(page, id).getByTestId("results-select").click();
      await page.getByTestId("result-reexport").click();
      await page.getByTestId("reexport-submit").click();
      await expect(page.getByTestId("reexport-result")).toHaveAttribute("data-state", "completed", { timeout: 20_000 });
      await page.close();
      await first.stop();

      const second = await serve(root, directory, true);
      page = await browser.newPage();
      await page.goto(`${second.url}/launch?session=${SESSION}`);
      await expect(page.getByTestId("app-ready")).toBeVisible();
      await page.getByTestId("mode-expert").click();
      await page.getByTestId("nav-results").click();
      await page.getByTestId("results-tab-history").click();
      await row(page, id).getByTestId("results-select").click();
      await page.getByTestId("result-reexport").click();
      await expect(page.getByTestId("reexport-history")).toContainText("1");
      await page.getByTestId("reexport-history").locator("summary").click();
      await expect(page.getByTestId("reexport-history").locator('[data-state="completed"]')).toHaveCount(1);
      await page.close();
      await second.stop();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
