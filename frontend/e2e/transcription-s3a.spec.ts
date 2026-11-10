import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * UI-3 S3-A against the real FastAPI app: `POST /api/jobs`, `GET /api/jobs/{id}`, `/cancel`,
 * `/events` and `/api/queue` run through the *real* JobManager/OperationQueue; only the transcribe
 * command is a synthetic process (tools/ui2/synthetic_jobs.py — no model download, no ASR, no
 * file written). The environment is the S2 synthetic profile CLI (cuda: faster-whisper with
 * "large-v3-turbo"/"small", devices CPU and one synthetic CUDA GPU).
 * Headless Chromium — NOT a WebView2 acceptance (native dialog/drop/IME are not exercised here).
 */

// Synthetic paths only (assembled at runtime); the synthetic process never opens them.
const MEDIA = ["", "synthetic", "media"].join("/");
const OUT = ["", "synthetic", "out"].join("/");
const input = (name: string) => `${MEDIA}/${name}`;

declare global {
  interface Window {
    __picks: string[];
    __nextPick: string | null;
  }
}

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const posts = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map(line);

/** The legacy queue is serial and process-wide: never leave a job running for the next test. */
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

async function withNativeBridge(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__picks = [];
    window.__nextPick = null;
    (window as unknown as { pywebview: unknown }).pywebview = {
      api: {
        choose_path: async (kind: string) => {
          window.__picks.push(kind);
          return window.__nextPick;
        },
        report_frontend_error: async () => true,
      },
    };
  });
  await page.goto(`/launch?session=${SESSION}`);
  await expect(page.getByTestId("app-ready")).toBeVisible();
}

/** cuda profile, typed input/output, model "small" on CPU. */
async function fillForm(page: Page, name: string, output = OUT): Promise<void> {
  await choose(page, "env-profile", /^cuda/);
  await expect(page.getByTestId("tx-profile")).toContainText("cuda");
  await page.getByTestId("tx-input-path").locator("input").fill(input(name));
  if (output !== "") await page.getByTestId("tx-output-path").locator("input").fill(output);
  await choose(page, "tx-model", /small/);
  await choose(page, "tx-device", /^CPU$/);
}

const status = (page: Page) => page.getByTestId("tx-status");

test("normal: one file → real POST once → GET is the status → completed with file names", async ({ app }) => {
  const seen = recordApi(app);
  // Before an environment answer exists there are no choices (no static fallback).
  await expect(app.getByTestId("tx-env-block")).toBeVisible();
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await expect(app.getByTestId("tx-output-no-default")).toBeVisible();
  await fillForm(app, "meeting.wav");
  await expect(app.getByTestId("tx-backend")).toContainText("faster-whisper"); // the only backend: filled
  await expect(app.getByTestId("tx-diarization-off")).toBeVisible();
  await expect(app.getByTestId("tx-unverified")).toBeVisible();
  await shot(app, "s3a-01-form");

  await app.getByTestId("tx-start").dblclick();
  await expect(status(app)).toHaveText(/実行中|開始待ち/);
  await expect(app.getByTestId("tx-snapshot")).toContainText("meeting.wav");
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await expect(app.getByTestId("tx-block")).toContainText("同時に1件");
  // Editing the form while running does not change the running job's snapshot.
  await app.getByTestId("tx-input-path").locator("input").fill(input("next.wav"));
  await expect(app.getByTestId("tx-snapshot")).toContainText("meeting.wav");
  await expect(app.getByTestId("tx-stage")).toContainText(/工程/);
  await shot(app, "s3a-02-running");

  await expect(status(app)).toHaveText("完了", { timeout: 20_000 });
  await expect(app.getByTestId("tx-outputs")).toContainText("出力ファイル: 3件");
  await expect(app.getByTestId("tx-outputs")).toContainText("meeting.srt");
  await expect(app.getByTestId("tx-outputs")).toContainText("meeting.json");
  await shot(app, "s3a-03-completed");

  expect(posts(seen).filter((p) => p === "POST /api/jobs")).toHaveLength(1);
  expect(posts(seen).filter((p) => p !== "POST /api/jobs")).toEqual([]); // no settings PATCH, no cancel
  expect(seen.some((r) => new URL(r.url()).pathname.startsWith("/api/v1"))).toBe(false);
  expect(seen.some((r) => /^\/api\/jobs\/[0-9a-f]{32}$/.test(new URL(r.url()).pathname))).toBe(true);
  // Body sent exactly once with diarization off and the legacy resume meaning.
  const body = seen.find((r) => line(r) === "POST /api/jobs")!.postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({ profile: "cuda", asr_backend: "faster-whisper", asr_model: "small", asr_device: "cpu", diarization_enabled: false, resume_mode: "resume", recursive: false, formats: ["srt", "json", "md"] });
  // Raw logs from the synthetic process never reach the page.
  expect(await app.evaluate(() => document.body.innerText)).not.toContain("synthetic raw log");
  // Nothing was put into browser storage or the URL.
  expect(await app.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(app.url()).not.toContain("synthetic");

  // Same input again is not offered in S3-A; another file is.
  await app.getByTestId("tx-input-path").locator("input").fill(input("meeting.wav"));
  await expect(app.getByTestId("tx-block")).toContainText("同じ入力ファイル");
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await app.getByTestId("tx-dismiss").first().click();
  await expect(app.getByTestId("tx-status-card")).toHaveCount(0);
  await expect(app.getByTestId("tx-block")).toContainText("同じ入力ファイル");
});

test("formats: srt+json only are sent; the saved default output folder is the initial value", async ({ app, request }) => {
  expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data: { default_output_dir: OUT } })).status()).toBe(200);
  await app.reload();
  await expect(app.getByTestId("tx-output-source")).toHaveText("設定の既定の出力先");
  await expect(app.getByTestId("tx-output-path").locator("input")).toHaveValue(OUT);
  const seen = recordApi(app);
  await fillForm(app, "formats.wav", "");
  await app.getByTestId("tx-format-md").locator("input").uncheck();
  await app.getByTestId("tx-start").click();
  await expect(status(app)).toHaveText("完了", { timeout: 20_000 });
  const body = seen.find((r) => line(r) === "POST /api/jobs")!.postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({ output_dir: OUT, formats: ["srt", "json"] });
});

test("failure: the guidance is a fixed text; 'done' with exit 0 followed by exit 3 is shown as failed", async ({ app }) => {
  await fillForm(app, "fail-input.wav");
  await app.getByTestId("tx-start").click();
  await expect(status(app)).toHaveText("失敗", { timeout: 20_000 });
  await expect(app.getByTestId("tx-guidance")).toHaveText("入力ファイルを読めませんでした。パスと形式を確認してください。");
  await expect(app.getByTestId("tx-exit-code")).toHaveText("終了コード: 4");
  expect(await app.evaluate(() => document.body.innerText)).not.toContain("cannot read");
  await shot(app, "s3a-04-failed");

  await app.getByTestId("tx-dismiss").first().click();
  await app.getByTestId("tx-input-path").locator("input").fill(input("lying-done.wav"));
  await app.getByTestId("tx-start").click();
  await expect(status(app)).toHaveText("失敗", { timeout: 20_000 });
  await expect(app.getByTestId("tx-exit-code")).toHaveText("終了コード: 3");
});

test("cancel: 202 is only 'requested'; 'cancelled' appears after GET; mode switches keep the tracking", async ({ app }) => {
  const seen = recordApi(app);
  await fillForm(app, "slow.wav");
  await app.getByTestId("tx-start").click();
  await expect(status(app)).toHaveText("実行中", { timeout: 15_000 });
  // Normal → Expert → settings → back: the same job is still followed.
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("pane-queue").getByTestId("tx-status")).toHaveText("実行中");
  await app.getByTestId("nav-settings").click();
  await app.getByTestId("nav-transcribe").click();
  await app.getByTestId("mode-normal").click();
  await expect(status(app)).toHaveText("実行中");
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue(input("slow.wav"));

  await app.getByTestId("tx-cancel").click();
  // The cancel answer itself may already be terminal (the tree kill is fast), so the transient
  // "requested" note is not asserted here; unit tests cover requested ≠ cancelled.
  await expect(status(app)).toHaveText("取消済み", { timeout: 15_000 });
  // The "requested" note is replaced by the GET-confirmed status (no stale "waiting" text).
  await expect(app.getByTestId("tx-cancel-state")).toHaveCount(0);
  await expect(app.getByTestId("tx-guidance")).toHaveText("取消されました。");
  await shot(app, "s3a-05-cancelled");
  expect(posts(seen).filter((p) => p.endsWith("/cancel"))).toHaveLength(1);
});

test("refusal: 400 creates no job and the server text (with a path) is not shown", async ({ app }) => {
  await fillForm(app, "reject.wav");
  await app.getByTestId("tx-start").click();
  await expect(app.getByTestId("tx-rejected")).toContainText("入力内容または実行プロファイルを受け付けませんでした。");
  await expect(app.getByTestId("tx-rejected")).toContainText("HTTP 400");
  expect(await app.evaluate(() => document.body.innerText)).not.toContain("synthetic-profile");
  await expect(app.getByTestId("tx-status-card")).toHaveCount(0);
  // A refusal did not start anything: the same input may be sent again explicitly.
  await expect(app.getByTestId("tx-start")).toBeEnabled();
});

test("lost POST answer: 'unknown', never resent; the queue is only a reference", async ({ app, request }) => {
  const seen = recordApi(app);
  // Same pattern as the S1 token test (PR #41): each browser Route is completed exactly once.
  // The intercepted POST body is sent once to the real FastAPI through the separate
  // APIRequestContext (it does not pass through page routes); its answer is recorded, and only
  // then is the browser request reset — the server accepted the job, the page never saw it.
  const applied: { status: number; id: string }[] = [];
  let handled = 0;
  await app.route("**/api/jobs", async (route) => {
    const original = route.request();
    if (original.method() !== "POST") return route.fallback();
    const response = await request.fetch("/api/jobs", {
      method: "POST",
      headers: { ...SESSION_HEADER, "Content-Type": "application/json" },
      data: original.postData() ?? undefined,
    });
    const body = (await response.json()) as { id?: string };
    applied.push({ status: response.status(), id: String(body.id ?? "") });
    await route.abort("connectionreset");
    handled += 1;
  });
  await fillForm(app, "slow.wav");
  await app.getByTestId("tx-start").click();
  await expect(app.getByTestId("tx-unknown")).toContainText("開始されたかどうか不明です");
  await expect.poll(() => handled).toBe(1);
  // The server really accepted exactly one job (202) for this one click.
  expect(applied.map((a) => a.status)).toEqual([202]);
  expect(applied[0]!.id).toMatch(/^[0-9a-f]{32}$/);
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await expect(app.getByTestId("tx-block")).toContainText("結果が不明");
  // No automatic resend: still one browser POST and one server-side POST after waiting.
  await app.waitForTimeout(3000);
  expect(posts(seen).filter((p) => p === "POST /api/jobs")).toHaveLength(1);
  expect(handled).toBe(1);
  await app.getByTestId("tx-check-queue").click();
  await expect(app.getByTestId("tx-queue-result")).toHaveText("受付済み・実行中の文字起こし: 1件");
  await shot(app, "s3a-06-unknown");
  await app.getByTestId("tx-acknowledge").click();
  await expect(app.getByTestId("tx-unknown")).toHaveCount(0);
  // The same input stays blocked (it may be running); nothing was resent.
  await expect(app.getByTestId("tx-block")).toContainText("同じ入力ファイル");
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  expect(posts(seen).filter((p) => p === "POST /api/jobs")).toHaveLength(1);
  expect(applied).toHaveLength(1);
  // The job the server accepted is the one in the queue (checked via the real API).
  const job = (await (await request.get(`/api/jobs/${applied[0]!.id}`, { headers: SESSION_HEADER })).json()) as { status: string };
  expect(["starting", "running"]).toContain(job.status);
});

test("SSE cut: polling alone follows the job to its GET-confirmed end; reconnects are bounded", async ({ app }) => {
  const seen = recordApi(app);
  await app.route("**/api/jobs/*/events", (route) => route.abort("connectionreset"));
  await fillForm(app, "meeting.wav");
  await app.getByTestId("tx-start").click();
  await expect(app.getByTestId("tx-live")).toContainText("定期的な状態確認", { timeout: 15_000 });
  await expect(status(app)).toHaveText("完了", { timeout: 20_000 });
  const sse = seen.filter((r) => new URL(r.url()).pathname.endsWith("/events"));
  expect(sse.length).toBeGreaterThanOrEqual(1);
  expect(sse.length).toBeLessThanOrEqual(3);
});

test("native drop (pywebview event) takes one file; several or a folder are refused; browser picker explains", async ({ app }) => {
  const dispatch = (detail: unknown) => app.evaluate((d) => window.dispatchEvent(new CustomEvent("utteran-input-dropped", { detail: d })), detail);
  await dispatch([
    { path: input("a.wav"), kind: "file" },
    { path: input("b.wav"), kind: "file" },
  ]);
  await expect(app.getByTestId("tx-drop-notice")).toContainText("2件がドロップされたため、どれも入力にしませんでした");
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue("");
  await dispatch([{ path: MEDIA, kind: "folder" }]);
  await expect(app.getByTestId("tx-drop-notice")).toContainText("フォルダは入力にできません");
  await dispatch([{ path: input("a.wav"), kind: "file" }]);
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue(input("a.wav"));
  await expect(app.getByTestId("tx-input-source")).toHaveText("ドロップで指定");
  await expect(app.getByTestId("tx-drop-notice")).toHaveCount(0);
  // The drop target the native shell binds exists before Vue renders.
  expect(await app.evaluate(() => document.querySelector("#input-drop-zone #app") !== null)).toBe(true);
  // On another page no input area listens: a drop there changes nothing.
  await app.getByTestId("nav-settings").click();
  await dispatch([{ path: input("c.wav"), kind: "file" }]);
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue(input("a.wav"));
  // Plain browser: no native dialog (pywebview never becomes ready).
  await app.getByTestId("tx-input-pick").click();
  await expect(app.getByTestId("tx-input-pick-error")).toContainText("ブラウザ表示ではOSのファイル選択を使えません", { timeout: 10_000 });
});

test("native dialogs: chosen file/folder fill the form; cancel changes nothing", async ({ app }) => {
  await withNativeBridge(app);
  await app.evaluate((p) => (window.__nextPick = p), input("picked.wav"));
  await app.getByTestId("tx-input-pick").click();
  await expect(app.getByTestId("tx-input-path").locator("input")).toHaveValue(input("picked.wav"));
  await expect(app.getByTestId("tx-input-source")).toHaveText("ファイル選択で指定");
  await app.evaluate(() => (window.__nextPick = null));
  await app.getByTestId("tx-output-pick").click();
  await expect(app.getByTestId("tx-output-path").locator("input")).toHaveValue("");
  await app.evaluate((p) => (window.__nextPick = p), OUT);
  await app.getByTestId("tx-output-pick").click();
  await expect(app.getByTestId("tx-output-path").locator("input")).toHaveValue(OUT);
  expect(await app.evaluate(() => window.__picks)).toEqual(["input_file", "output_folder", "output_folder"]);
});

test("unsafe values: a relative path is refused before sending; HTML-looking names stay text", async ({ app }) => {
  const seen = recordApi(app);
  await fillForm(app, "x.wav");
  await app.getByTestId("tx-input-path").locator("input").fill("relative/x.wav");
  await expect(app.getByTestId("tx-input-path")).toContainText("絶対パス");
  await expect(app.getByTestId("tx-start")).toBeDisabled();
  await app.getByTestId("tx-input-path").locator("input").fill(input("<img src=x onerror=alert(1)>.wav"));
  await app.getByTestId("tx-start").click();
  await expect(status(app)).toHaveText("完了", { timeout: 20_000 });
  await expect(app.getByTestId("tx-outputs")).toContainText("<img src=x onerror=alert(1)>.srt");
  expect(await app.locator("img[src='x']").count()).toBe(0);
  expect(posts(seen).filter((p) => p === "POST /api/jobs")).toHaveLength(1);
});
