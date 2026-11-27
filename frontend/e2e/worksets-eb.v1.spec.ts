import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";
import type { APIRequestContext, Page } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER, fillOutput, revealLibrary } from "./fixtures";
import { test } from "./isolatedV1";

/**
 * Bundle E-B against the real FastAPI app in the `--next-ui-v1` shape: the real worksets store
 * (its own SQLite file below the synthetic data root), real B5/B7, the browser's own fetch.
 * Synthetic paths only. Headless Chromium — NOT a WebView2 acceptance (UI-4 #22).
 */

const input = (root: string, name: string) => join(root, "in", name);

async function jobCount(request: APIRequestContext): Promise<number> {
  const response = await request.get("/api/v1/jobs", { headers: SESSION_HEADER });
  expect(response.status()).toBe(200);
  return ((await response.json()) as { items: unknown[] }).items.length;
}

async function saved(page: Page) {
  await expect(page.getByTestId("ws-status")).toHaveAttribute("data-phase", "clean", { timeout: 10_000 });
}

async function addTyped(page: Page, path: string) {
  await page.getByTestId("tx-input-path").locator("input").fill(path);
  await page.getByTestId("tx-input-add-typed").click();
}

async function libraries(request: APIRequestContext, name: string) {
  const policy = await request.post("/api/v1/execution-policies", {
    headers: { ...SESSION_HEADER, "Idempotency-Key": `eb-policy-${name}-0001`.padEnd(20, "0") },
    data: { schema_version: 1, name: `${name} CPU`, mode: "auto", fixed_candidate_id: null, allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null, fallback: "none", execution_deadline_seconds: null },
  });
  expect(policy.status()).toBe(201);
  return (await policy.json()) as { id: string };
}

async function createPreset(page: Page, name: string) {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  await page.getByTestId("v1-preset-create-open").click();
  await page.getByTestId("v1-preset-name").locator("input").fill(name);
  await page.getByTestId("v1-preset-check").click();
  await page.getByTestId("v1-preset-save").click();
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(name);
}

test("first run, Normal → Expert, autosave and reload restore the same workset without creating jobs", async ({ app, request, backend }) => {
  test.setTimeout(60_000);
  const bar = app.getByTestId("ws-bar");
  await expect(bar.getByTestId("ws-select")).toContainText("作業セット1");
  await saved(app);
  await libraries(request, "EB");
  await app.getByTestId("v1-lib-reload").click();
  await createPreset(app, "EB 合成");
  await choose(app, "v1-policy-select", /EB CPU/);
  await addTyped(app, input(backend.root, "meeting.wav"));
  await addTyped(app, input(backend.root, "other.wav"));
  await fillOutput(app, join(backend.root, "out"));
  // A half-typed path is not saved.
  await app.getByTestId("tx-input-path").locator("input").fill(input(backend.root, "half-typed.wav"));
  await saved(app);

  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("ws-select")).toContainText("作業セット1");
  const rows = app.getByTestId("tx-list-row");
  await expect(rows).toHaveCount(2);
  await rows.filter({ hasText: "meeting.wav" }).getByTestId("tx-list-check").click();
  await saved(app);
  const before = await request.get("/api/v1/worksets", { headers: SESSION_HEADER });
  const listing = (await before.json()) as { items: { id: string; revision: number }[]; active: { view: { mode: string } } };
  expect(listing.items).toHaveLength(1);
  expect(listing.active.view.mode).toBe("expert");
  const stored = await request.get(`/api/v1/worksets/${listing.items[0]!.id}`, { headers: SESSION_HEADER });
  const text = await stored.text();
  expect(text).not.toContain("half-typed");
  expect(text).not.toContain("token");

  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await expect(app.locator("[data-mode]").first()).toHaveAttribute("data-mode", "expert");
  await saved(app);
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "meeting.wav" }).getByTestId("tx-list-check").locator("input")).not.toBeChecked();
  await expect(rows.filter({ hasText: "other.wav" }).getByTestId("tx-list-check").locator("input")).toBeChecked();
  await revealLibrary(app);
  await expect(app.getByTestId("v1-preset-frozen")).toContainText("EB 合成");
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue("");
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("ws-select")).toContainText("作業セット1");
  // Restoring alone never submits, resumes or re-runs anything.
  expect(await jobCount(request)).toBe(0);
  const after = (await (await request.get("/api/v1/worksets", { headers: SESSION_HEADER })).json()) as typeof listing;
  expect(after.items[0]!.revision).toBe(listing.items[0]!.revision);
});

test("new, rename, switch and delete keep each workset's rows; deleting changes no job or output", async ({ app, request, backend }) => {
  test.setTimeout(90_000);
  await libraries(request, "EBD");
  await app.getByTestId("v1-lib-reload").click();
  await createPreset(app, "EBD 合成");
  await choose(app, "v1-policy-select", /EBD CPU/);
  await addTyped(app, input(backend.root, "meeting.wav"));
  await fillOutput(app, join(backend.root, "out"));
  await saved(app);
  // One real job on the first workset.
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  const jobsBefore = await (await request.get("/api/v1/jobs", { headers: SESSION_HEADER })).json();
  expect(jobsBefore.items).toHaveLength(1);

  // Rename (IME composition: Enter while composing does not submit).
  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-rename").click();
  const name = app.getByTestId("ws-rename-input").locator("input");
  await name.fill("案件A");
  await name.dispatchEvent("compositionstart", { data: "A" });
  await name.press("Enter");
  await name.dispatchEvent("compositionend", { data: "A" });
  await expect(app.getByTestId("ws-rename-dialog")).toBeVisible();
  await app.getByTestId("ws-rename-save").click();
  await saved(app);
  await expect(app.getByTestId("ws-select")).toContainText("案件A");

  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-new").click();
  await expect(app.getByTestId("ws-select")).toContainText("作業セット1");
  await expect(app.getByTestId("tx-input-none")).toBeVisible();
  await addTyped(app, input(backend.root, "other.wav"));
  await saved(app);

  await choose(app, "ws-select", /案件A/);
  await expect(app.getByTestId("tx-input-current")).toContainText("meeting.wav");
  await choose(app, "ws-select", /作業セット1/);
  await expect(app.getByTestId("tx-input-current")).toContainText("other.wav");

  // Delete the current workset after the confirmation; the first one opens again.
  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-delete").click();
  await expect(app.getByTestId("ws-delete-dialog")).toContainText("ジョブ・キュー・履歴は削除されません");
  await app.getByTestId("ws-delete-cancel").click();
  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-delete").click();
  await app.getByTestId("ws-delete-confirm").click();
  await expect(app.getByTestId("ws-select")).toContainText("案件A");
  await expect(app.getByTestId("tx-input-current")).toContainText("meeting.wav");
  const jobsAfter = await (await request.get("/api/v1/jobs", { headers: SESSION_HEADER })).json();
  expect(jobsAfter.items.map((j: { id: string; snapshot: unknown }) => [j.id, j.snapshot])).toEqual(jobsBefore.items.map((j: { id: string; snapshot: unknown }) => [j.id, j.snapshot]));
  const list = (await (await request.get("/api/v1/worksets", { headers: SESSION_HEADER })).json()) as { items: { name: string }[] };
  expect(list.items.map((i) => i.name)).toEqual(["案件A"]);
  // The last workset cannot be deleted.
  await app.getByTestId("ws-menu").click();
  await expect(app.getByTestId("ws-delete")).toHaveClass(/v-list-item--disabled/);
});

test("an input added while another workset is being opened is kept and saved, never replaced", async ({ app, request, backend }) => {
  test.setTimeout(60_000);
  await saved(app);
  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-new").click();
  await expect(app.getByTestId("ws-select")).toContainText("作業セット2");
  await saved(app);
  const listing = (await (await request.get("/api/v1/worksets", { headers: SESSION_HEADER })).json()) as { items: { id: string; name: string }[] };
  const first = listing.items.find((i) => i.name === "作業セット1")!.id;
  const second = listing.items.find((i) => i.name === "作業セット2")!.id;
  // Hold the read of the other workset until an input was added on this one.
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await app.route(`**/api/v1/worksets/${first}`, async (route) => {
    if (route.request().method() === "GET") await gate;
    await route.continue();
  });
  await app.getByTestId("ws-select").click();
  await app.getByRole("option", { name: /作業セット1/ }).click();
  await addTyped(app, input(backend.root, "meeting.wav"));
  release();
  await expect(app.getByTestId("ws-block")).toContainText("切り替えを中止しました");
  await expect(app.getByTestId("ws-select")).toContainText("作業セット2");
  await expect(app.getByTestId("tx-input-current")).toContainText("meeting.wav");
  await saved(app);
  const stored = (await (await request.get(`/api/v1/worksets/${second}`, { headers: SESSION_HEADER })).json()) as { state: { inputs: { input_path: string }[] } };
  expect(stored.state.inputs.map((i) => i.input_path)).toEqual([input(backend.root, "meeting.wav")]);
  await app.unroute(`**/api/v1/worksets/${first}`);
});

test("while another workset is being opened, the v1 check cannot start and nothing is validated", async ({ app, request, backend }) => {
  test.setTimeout(60_000);
  await libraries(request, "EBX");
  await app.getByTestId("v1-lib-reload").click();
  await createPreset(app, "EBX 合成");
  await choose(app, "v1-policy-select", /EBX CPU/);
  await addTyped(app, input(backend.root, "meeting.wav"));
  await fillOutput(app, join(backend.root, "out"));
  await saved(app);
  await app.getByTestId("ws-menu").click();
  await app.getByTestId("ws-new").click();
  await expect(app.getByTestId("ws-select")).toContainText("作業セット2");
  await saved(app);
  const listing = (await (await request.get("/api/v1/worksets", { headers: SESSION_HEADER })).json()) as { items: { id: string; name: string }[] };
  const first = listing.items.find((i) => i.name === "作業セット1")!.id;
  const validations: string[] = [];
  app.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/v1/validations") validations.push(r.method());
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await app.route(`**/api/v1/worksets/${first}`, async (route) => {
    if (route.request().method() === "GET") await gate;
    await route.continue();
  });
  await choose(app, "ws-select", /作業セット1/);
  await expect(app.getByTestId("v1-block")).toContainText("作業セットの切替");
  await expect(app.getByTestId("v1-check")).toBeDisabled();
  release();
  await expect(app.getByTestId("tx-input-current")).toContainText("meeting.wav");
  await expect(app.getByTestId("v1-block")).toHaveCount(0);
  await expect(app.getByTestId("v1-check")).toBeEnabled();
  expect(validations).toEqual([]);
  await app.unroute(`**/api/v1/worksets/${first}`);
});

test("a restored input that disappeared stays listed as missing and blocks the start until it is back", async ({ app, backend }) => {
  test.setTimeout(60_000);
  const gone = input(backend.root, "later.wav");
  await addTyped(app, gone);
  await saved(app);
  await app.reload();
  await saved(app);
  await expect(app.getByTestId("tx-input-missing")).toHaveText("見つかりません");
  await expect(app.getByTestId("ws-missing")).toHaveAttribute("data-missing", "1");
  await expect(app.getByTestId("v1-missing")).toContainText("ファイルが見つかりません");
  await expect(app.getByTestId("tx-input-current")).toContainText("later.wav");
  await writeFile(gone, "synthetic");
  await app.getByTestId("ws-recheck").click();
  await expect(app.getByTestId("tx-input-missing")).toHaveCount(0);
  await expect(app.getByTestId("ws-missing")).toHaveCount(0);
});

test("two windows: the second write is a conflict and is only applied after an explicit choice", async ({ app, backend, context }) => {
  test.setTimeout(60_000);
  await saved(app);
  const other = await context.newPage();
  await other.goto(`/launch?session=${SESSION}`);
  await expect(other.getByTestId("app-ready")).toBeVisible();
  await saved(other);
  await addTyped(app, input(backend.root, "meeting.wav"));
  await saved(app);
  await addTyped(other, input(backend.root, "other.wav"));
  await expect(other.getByTestId("ws-conflict")).toHaveAttribute("data-kind", "remote_changed");
  await expect(other.getByTestId("ws-status")).toHaveAttribute("data-phase", "conflict");
  // Switching away is refused while the conflict is open.
  await other.getByTestId("ws-menu").click();
  await other.getByTestId("ws-new").click();
  await expect(other.getByTestId("ws-block")).toContainText("競合を解決するまで");
  await other.getByTestId("ws-conflict-keep").click();
  await saved(other);
  await app.reload();
  await saved(app);
  await expect(app.getByTestId("tx-input-current")).toContainText("other.wav");
  await other.close();
});

test("the saved Preset revision stays frozen after a newer revision and after deletion", async ({ app, request }) => {
  test.setTimeout(60_000);
  await libraries(request, "EBR");
  await app.getByTestId("v1-lib-reload").click();
  await createPreset(app, "EBR 合成");
  await saved(app);
  const presets = (await (await request.get("/api/v1/presets", { headers: SESSION_HEADER })).json()) as { items: { id: string; revision: number; name: string; settings: unknown }[] };
  const preset = presets.items.find((p) => p.name === "EBR 合成")!;
  const revised = await request.put(`/api/v1/presets/${preset.id}`, {
    headers: { ...SESSION_HEADER, "If-Match": '"1"' },
    data: { schema_version: 1, name: "EBR 改訂", settings: preset.settings },
  });
  expect(revised.status()).toBe(200);
  await app.reload();
  await saved(app);
  await expect(app.getByTestId("v1-preset-frozen")).toContainText("EBR 合成");
  await expect(app.getByTestId("v1-preset-newer")).toBeVisible();
  const removed = await request.delete(`/api/v1/presets/${preset.id}`, { headers: { ...SESSION_HEADER, "If-Match": '"2"' } });
  expect(removed.status()).toBe(204);
  await app.reload();
  await saved(app);
  // B5 keeps the past revision readable: still rev 1, shown as missing from the list, never "latest".
  await expect(app.getByTestId("v1-preset-frozen")).toContainText("EBR 合成");
  await expect(app.getByTestId("v1-preset-missing")).toBeVisible();
  const list = (await (await request.get("/api/v1/worksets", { headers: SESSION_HEADER })).json()) as { items: { id: string }[] };
  const doc = (await (await request.get(`/api/v1/worksets/${list.items[0]!.id}`, { headers: SESSION_HEADER })).json()) as { state: { preset_ref: { id: string; revision: number } } };
  expect(doc.state.preset_ref).toEqual({ id: preset.id, revision: 1 });
});

test("English labels and fixed codes; no private value in the URL or browser storage", async ({ app, request, backend }) => {
  const patched = await request.patch("/api/settings", { headers: SESSION_HEADER, data: { language: "en" } });
  expect(patched.status()).toBe(200);
  await app.reload();
  await saved(app);
  await expect(app.getByTestId("ws-bar")).toContainText("Workset");
  await expect(app.getByTestId("ws-status")).toContainText("Saved");
  await addTyped(app, input(backend.root, "meeting.wav"));
  await saved(app);
  const storage = await app.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }));
  expect(storage).not.toContain("meeting.wav");
  expect(storage).not.toContain(backend.root);
});

// ---- application restart on the same data root ---------------------------------------------

async function serve(root: string, directory: string, reuse: boolean) {
  const args = ["run", "--no-sync", "python", "../tools/ui2/serve_next_ui.py", "--port", "0", "--v1", "--data-root", root, "--exit-on-stdin-close"];
  if (reuse) args.push("--reuse-data-root");
  const child = spawn("uv", args, { env: { ...process.env, TMPDIR: directory, TEMP: directory, TMP: directory }, stdio: ["pipe", "pipe", "inherit"] });
  const closed = new Promise<number | null>((resolve) => child.once("close", resolve));
  const lines = createInterface({ input: child.stdout });
  const url = await new Promise<string>((resolve, reject) => {
    child.once("exit", (code) => reject(new Error(`server exited (${code})`)));
    lines.on("line", (line) => {
      const match = /^http:\/\/127\.0\.0\.1:\d+(?=\/launch\?)/.exec(line);
      if (match) resolve(match[0]);
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
  test("the last workset and display mode are restored after the application restarts", async ({ browser }) => {
    test.setTimeout(120_000);
    const directory = await mkdtemp(join(tmpdir(), "utteran-eb-e2e-"));
    const root = join(directory, "v1");
    try {
      const first = await serve(root, directory, false);
      let page = await browser.newPage();
      await page.goto(`${first.url}/launch?session=${SESSION}`);
      await saved(page);
      await addTyped(page, input(root, "meeting.wav"));
      await page.getByTestId("mode-expert").click();
      await saved(page);
      await page.waitForTimeout(800); // the active pointer is written shortly after the mode change
      await page.close();
      await first.stop();

      const second = await serve(root, directory, true);
      page = await browser.newPage();
      await page.goto(`${second.url}/launch?session=${SESSION}`);
      await saved(page);
      await expect(page.locator("[data-mode]").first()).toHaveAttribute("data-mode", "expert");
      await expect(page.getByTestId("tx-list-row")).toHaveCount(1);
      await expect(page.getByTestId("tx-list-row")).toContainText("meeting.wav");
      const jobs = await page.request.get(`${second.url}/api/v1/jobs`, { headers: SESSION_HEADER });
      expect(((await jobs.json()) as { items: unknown[] }).items).toHaveLength(0);
      await page.close();
      await second.stop();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
