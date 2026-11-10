import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * UI-3 S3-B1 against the real FastAPI app: several inputs are sent one by one through the *real*
 * legacy `POST /api/jobs` → JobManager → OperationQueue; only the transcribe command is a
 * synthetic process (tools/ui2/synthetic_jobs.py — no model, no ASR, no file written).
 * Headless Chromium — NOT a WebView2 acceptance, and a synthetic CLI says nothing about real models.
 */

const MEDIA = ["", "synthetic", "media"].join("/");
const OUT = ["", "synthetic", "out"].join("/");
const input = (name: string) => `${MEDIA}/${name}`;

interface Recorder {
  seen: Request[];
  /** Most `POST /api/jobs` the page had open at the same time. */
  maxInFlight: () => number;
}

function recordApi(page: Page): Recorder {
  const seen: Request[] = [];
  let open = 0;
  let max = 0;
  const isStart = (r: Request) => r.method() === "POST" && new URL(r.url()).pathname === "/api/jobs";
  page.on("request", (r) => {
    if (!new URL(r.url()).pathname.startsWith("/api/")) return;
    seen.push(r);
    if (isStart(r)) max = Math.max(max, ++open);
  });
  const done = (r: Request) => {
    if (isStart(r)) open -= 1;
  };
  page.on("requestfinished", done);
  page.on("requestfailed", done);
  return { seen, maxInFlight: () => max };
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const starts = (rec: Recorder) => rec.seen.filter((r) => line(r) === "POST /api/jobs");
const bodies = (rec: Recorder) => starts(rec).map((r) => r.postDataJSON() as Record<string, unknown>);

async function drainQueue(request: APIRequestContext): Promise<void> {
  const items = (await (await request.get("/api/queue", { headers: SESSION_HEADER })).json()) as { id: string; status: string }[];
  for (const item of items) {
    if (item.status === "waiting" || item.status === "running") await request.post(`/api/queue/${item.id}/cancel`, { headers: SESSION_HEADER });
  }
  await expect
    .poll(async () => ((await (await request.get("/api/queue", { headers: SESSION_HEADER })).json()) as { status: string }[]).filter((i) => i.status === "waiting" || i.status === "running").length)
    .toBe(0);
}
test.afterEach(async ({ request }) => drainQueue(request));

/** Saved default output → reload → Expert → cuda profile, common model "small" on CPU. */
async function expert(app: Page, request: APIRequestContext): Promise<void> {
  expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data: { default_output_dir: OUT } })).status()).toBe(200);
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  await choose(app, "env-profile", /^cuda/);
  await expect(app.getByTestId("tx-profile")).toContainText("cuda");
  await choose(app, "tx-model", /small/);
  await choose(app, "tx-device", /^CPU$/);
  await expect(app.getByTestId("tx-output-path").locator("input")).toHaveValue(OUT);
}

async function drop(app: Page, names: string[]): Promise<void> {
  await app.evaluate((d) => window.dispatchEvent(new CustomEvent("utteran-input-dropped", { detail: d })), names.map((n) => ({ path: input(n), kind: "file" })));
}
const row = (app: Page, name: string) => app.locator(`[data-testid="tx-list-row"][data-name="${name}"]`);
async function selectOnly(app: Page, name: string): Promise<void> {
  await app.getByTestId("tx-list-select-none").click();
  await row(app, name).getByTestId("tx-list-check").click();
}
const counts = (app: Page) => app.getByTestId("tx-batch-counts");
const phases = async (app: Page) => app.getByTestId("tx-batch-item").evaluateAll((items) => items.map((i) => i.getAttribute("data-phase")));

test("several files: common settings + one model and one folder per file → three real POSTs, one at a time, as frozen", async ({ app, request }) => {
  const rec = recordApi(app);
  await expert(app, request);
  await drop(app, ["a.wav", "b.wav", "c.wav"]);
  await expect(app.getByTestId("tx-list-count")).toHaveText("3件（選択 3件）");
  // b: its own model; c: its own output folder (both only for that file).
  await selectOnly(app, "b.wav");
  await choose(app, "tx-ov-model", /large-v3-turbo/);
  await app.getByTestId("tx-ov-apply").click();
  await selectOnly(app, "c.wav");
  await app.getByTestId("tx-ov-output-custom").click();
  await app.getByTestId("tx-ov-output-path").locator("input").fill(`${OUT}/c`);
  await app.getByTestId("tx-ov-apply").click();
  await expect(row(app, "b.wav").getByTestId("tx-list-overridden")).toBeVisible();
  await expect(row(app, "c.wav").getByTestId("tx-list-overridden")).toBeVisible();
  await expect(row(app, "a.wav").getByTestId("tx-list-overridden")).toHaveCount(0);
  // A mixed selection is shown as mixed, not as the first file's value.
  await app.getByTestId("tx-list-select-all").click();
  await expect(app.getByTestId("tx-ov-model")).toContainText("3件で値が混在");
  await shot(app, "s3b1-01-overrides");

  await app.getByTestId("tx-batch-start").dblclick();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("すべての開始要求に受付の応答がありました", { timeout: 30_000 });
  await expect(counts(app)).toHaveText("受付済み 3件・受信拒否 0件・結果不明 0件・未投入 0件");
  expect(await phases(app)).toEqual(["accepted", "accepted", "accepted"]);
  await shot(app, "s3b1-02-accepted");

  expect(starts(rec)).toHaveLength(3);
  expect(rec.maxInFlight()).toBe(1);
  const sent = bodies(rec);
  expect(sent.map((b) => b.input_path)).toEqual([input("a.wav"), input("b.wav"), input("c.wav")]);
  expect(sent.map((b) => [b.asr_model, b.output_dir])).toEqual([
    ["small", OUT],
    ["large-v3-turbo", OUT],
    ["small", `${OUT}/c`],
  ]);
  for (const b of sent) expect(b).toMatchObject({ profile: "cuda", asr_backend: "faster-whisper", asr_device: "cpu", diarization_enabled: false, resume_mode: "resume", recursive: false, language: "ja", formats: ["srt", "json", "md"] });
  // Each row shows its own server job id; the real queue knows exactly these jobs.
  const ids = await app.getByTestId("tx-list-job-id").allInnerTexts();
  const jobIds = ids.map((t) => t.replace(/^.*: /, ""));
  expect(new Set(jobIds).size).toBe(3);
  for (const id of jobIds) expect(id).toMatch(/^[0-9a-f]{32}$/);
  const queue = (await (await request.get("/api/queue", { headers: SESSION_HEADER })).json()) as { id: string; kind: string }[];
  for (const id of jobIds) expect(queue.find((q) => q.id === id)?.kind).toBe("transcription");
  // The legacy side effect: the last accepted file's folder is now the server default (not hidden, not undone).
  const settings = (await (await request.get("/api/settings", { headers: SESSION_HEADER })).json()) as { default_output_dir: string };
  expect(settings.default_output_dir).toBe(`${OUT}/c`);
  await expect(app.getByTestId("tx-batch-remember")).toBeVisible();
  await expect.poll(() => rec.seen.filter((r) => line(r) === "GET /api/settings").length).toBeGreaterThanOrEqual(1); // re-read after the batch
  expect(rec.seen.filter((r) => r.method() === "PATCH")).toEqual([]);
  // B1 does not follow many jobs: no GET per batch job, and the S3-A detail card is not used.
  expect(rec.seen.some((r) => /^\/api\/jobs\/[0-9a-f]{32}$/.test(new URL(r.url()).pathname))).toBe(false);
  await expect(app.getByTestId("tx-status-card")).toHaveCount(0);
  await expect(app.getByTestId("tx-batch-not-tracking")).toBeVisible();
  // Accepted rows cannot be removed or sent again; mode switches send nothing.
  await expect(app.getByTestId("tx-list-remove")).toBeDisabled();
  await expect(app.getByTestId("tx-batch-start")).toBeDisabled();
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("tx-input-summary")).toContainText("受付済み 3");
  await expect(app.getByTestId("tx-block")).toContainText("同じ入力ファイル");
  await app.getByTestId("mode-expert").click();
  expect(starts(rec)).toHaveLength(3);
  expect(rec.seen.some((r) => new URL(r.url()).pathname.startsWith("/api/v1"))).toBe(false);
  expect(await app.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(app.url()).not.toContain("synthetic");
});

test("a received refusal stops the rest: 1 accepted, 1 refused, 1 not sent; continuing sends only the unsent file", async ({ app, request }) => {
  const rec = recordApi(app);
  await expert(app, request);
  await drop(app, ["one.wav", "reject.wav", "three.wav"]);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("投入を途中で止めました", { timeout: 30_000 });
  await expect(counts(app)).toHaveText("受付済み 1件・受信拒否 1件・結果不明 0件・未投入 1件");
  expect(await phases(app)).toEqual(["accepted", "rejected", "not_sent"]);
  await expect(app.getByTestId("tx-batch-stop-reason")).toContainText("受け付けられなかったため");
  await expect(app.getByTestId("tx-batch-item-error")).toContainText("HTTP 400");
  expect(await app.evaluate(() => document.body.innerText)).not.toContain("synthetic-profile");
  await shot(app, "s3b1-03-refused");
  await app.waitForTimeout(1500);
  expect(starts(rec)).toHaveLength(2);
  // Only the never-sent row is a target now (the refused one is not resent automatically).
  await expect(app.getByTestId("tx-batch-start")).toHaveText(/1件/);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("すべての開始要求に受付の応答がありました", { timeout: 30_000 });
  expect(bodies(rec).map((b) => b.input_path)).toEqual([input("one.wav"), input("reject.wav"), input("three.wav")]);
  // Removing is display-only and only for rows that were never accepted.
  await app.getByTestId("tx-list-select-all").click();
  await expect(app.getByTestId("tx-list-remove")).toHaveText(/1件/);
  await app.getByTestId("tx-list-remove").click();
  await expect(app.getByTestId("tx-list-remove-confirm")).toContainText("ジョブの取消や、成果物・履歴の削除は行いません");
  await app.getByTestId("tx-list-remove-yes").click();
  await expect(app.getByTestId("tx-list-count")).toHaveText(/^2件/);
  expect(rec.seen.filter((r) => r.method() !== "GET" && line(r) !== "POST /api/jobs")).toEqual([]);
});

test("a lost answer stops at once as 'unknown' and is never resent; the server really accepted it", async ({ app, request }) => {
  const rec = recordApi(app);
  await expert(app, request);
  // Same pattern as S1/S3-A: each browser Route is completed exactly once. Only the 2nd POST is
  // sent to the real FastAPI through the separate APIRequestContext, then the browser request is
  // reset — the server accepted the job, the page never saw the answer.
  const applied: { status: number; id: string }[] = [];
  let seen = 0;
  await app.route("**/api/jobs", async (route) => {
    const original = route.request();
    if (original.method() !== "POST") return route.fallback();
    seen += 1;
    if (seen !== 2) return route.fallback();
    const response = await request.fetch("/api/jobs", { method: "POST", headers: { ...SESSION_HEADER, "Content-Type": "application/json" }, data: original.postData() ?? undefined });
    const body = (await response.json()) as { id?: string };
    applied.push({ status: response.status(), id: String(body.id ?? "") });
    await route.abort("connectionreset");
  });
  await drop(app, ["first.wav", "slow-lost.wav", "third.wav"]);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("投入を途中で止めました", { timeout: 30_000 });
  expect(await phases(app)).toEqual(["accepted", "unknown", "not_sent"]);
  await expect(app.getByTestId("tx-batch-stop-reason")).toContainText("自動で再送・照合はしません");
  expect(applied.map((a) => a.status)).toEqual([202]);
  expect(applied[0]!.id).toMatch(/^[0-9a-f]{32}$/);
  await shot(app, "s3b1-04-unknown");
  await app.waitForTimeout(3000);
  expect(starts(rec)).toHaveLength(2);
  await expect(row(app, "slow-lost.wav").getByTestId("tx-row-status")).toHaveText("結果不明");
  // Continuing targets only the never-sent row.
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("すべての開始要求に受付の応答がありました", { timeout: 30_000 });
  expect(bodies(rec).map((b) => b.input_path)).toEqual([input("first.wav"), input("slow-lost.wav"), input("third.wav")]);
  expect(seen).toBe(3);
  expect(applied).toHaveLength(1);
  // The unknown row stays: it cannot be removed (it may be running).
  await selectOnly(app, "slow-lost.wav");
  await expect(app.getByTestId("tx-list-remove")).toBeDisabled();
  const job = (await (await request.get(`/api/jobs/${applied[0]!.id}`, { headers: SESSION_HEADER })).json()) as { status: string };
  expect(["starting", "running"]).toContain(job.status);
});

test("stop while a POST is in flight: it is not aborted; edits meanwhile do not change what was frozen; no single start can slip in", async ({ app, request }) => {
  const rec = recordApi(app);
  await expert(app, request);
  // Hold the first start request for a moment (then pass it on unchanged, once).
  let held = 0;
  await app.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    held += 1;
    if (held === 1) await new Promise((resolve) => setTimeout(resolve, 2500));
    return route.fallback();
  });
  await drop(app, ["hold-1.wav", "hold-2.wav", "hold-3.wav"]);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("投入中（1件ずつ送信しています）");
  // Normal mode while the batch runs: the single start is refused (shared door).
  await app.getByTestId("mode-normal").click();
  await expect(app.getByTestId("tx-input-batch")).toContainText("順次投入中");
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await expect(app.getByTestId("tx-block")).toContainText(/順次投入の実行中|同時に1件|同じ入力ファイル/);
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("tx-batch-stop").click();
  await expect(app.getByTestId("tx-batch-phase")).toContainText("その要求は中断しません");
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("投入を途中で止めました", { timeout: 30_000 });
  await expect(counts(app)).toHaveText("受付済み 1件・受信拒否 0件・結果不明 0件・未投入 2件");
  await expect(app.getByTestId("tx-batch-stop-reason")).toContainText("受付済みのジョブは取り消していません");
  await app.waitForTimeout(1500);
  expect(starts(rec)).toHaveLength(1);
  expect(rec.seen.filter((r) => r.method() === "POST" && r.url().includes("/cancel"))).toEqual([]);
  await shot(app, "s3b1-05-stopped");

  // Edits now apply to the next batch only; the next batch freezes them when it starts.
  await choose(app, "tx-language", /英語/);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("すべての開始要求に受付の応答がありました", { timeout: 30_000 });
  const sent = bodies(rec);
  expect(sent.map((b) => [b.input_path, b.language])).toEqual([
    [input("hold-1.wav"), "ja"],
    [input("hold-2.wav"), "en"],
    [input("hold-3.wav"), "en"],
  ]);
  expect(rec.maxInFlight()).toBe(1);
});

test("frozen at start: settings changed while the first POST is held never reach the later bodies", async ({ app, request }) => {
  const rec = recordApi(app);
  await expert(app, request);
  let held = 0;
  await app.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    held += 1;
    if (held === 1) await new Promise((resolve) => setTimeout(resolve, 2500));
    return route.fallback();
  });
  await drop(app, ["frozen-1.wav", "frozen-2.wav"]);
  await app.getByTestId("tx-batch-start").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("投入中（1件ずつ送信しています）");
  // The common language, formats and the second file's own folder change while it waits.
  await choose(app, "tx-language", /英語/);
  await app.getByTestId("tx-format-vtt").locator("input").check();
  await selectOnly(app, "frozen-2.wav");
  await app.getByTestId("tx-ov-output-custom").click();
  await app.getByTestId("tx-ov-output-path").locator("input").fill(`${OUT}/late`);
  await app.getByTestId("tx-ov-apply").click();
  await expect(app.getByTestId("tx-batch-phase")).toHaveText("すべての開始要求に受付の応答がありました", { timeout: 30_000 });
  expect(bodies(rec).map((b) => [b.language, b.output_dir, b.formats])).toEqual([
    ["ja", OUT, ["srt", "json", "md"]],
    ["ja", OUT, ["srt", "json", "md"]],
  ]);
});

test("adding: several drops, a folder and duplicates are explained; typed paths need Add; the limit is stated", async ({ app }) => {
  await app.getByTestId("mode-expert").click();
  await app.evaluate(
    (d) => window.dispatchEvent(new CustomEvent("utteran-input-dropped", { detail: d })),
    [
      { path: input("x.wav"), kind: "file" },
      { path: MEDIA, kind: "folder" },
      { path: input("odd"), kind: "unknown" },
    ],
  );
  await expect(app.getByTestId("tx-list-notice")).toHaveText("ドロップ: 1件を追加しました。追加しなかった項目 — 重複 0件、フォルダ 1件、種類不明 1件、使えないパス 0件。");
  await app.getByTestId("tx-input-path").locator("input").fill("C:\\Media\\Y.wav");
  await app.getByTestId("tx-input-add-typed").click();
  await app.getByTestId("tx-input-path").locator("input").fill("c:/media/y.WAV");
  await app.getByTestId("tx-input-add-typed").click();
  await expect(app.getByTestId("tx-list-notice")).toContainText("既に一覧にある");
  await expect(app.getByTestId("tx-list-count")).toHaveText("2件（選択 2件）");
  await expect(app.getByTestId("tx-input-limit")).toContainText("100件");
  // The typed field keeps a refused value for correction and Enter adds a valid one.
  await app.getByTestId("tx-input-path").locator("input").fill("relative.wav");
  await expect(app.getByTestId("tx-input-add-typed")).toBeDisabled();
  await app.getByTestId("tx-input-path").locator("input").fill(input("z.wav"));
  await app.getByTestId("tx-input-path").locator("input").press("Enter");
  await expect(app.getByTestId("tx-list-count")).toHaveText("3件（選択 3件）");
  await shot(app, "s3b1-06-list");
});
