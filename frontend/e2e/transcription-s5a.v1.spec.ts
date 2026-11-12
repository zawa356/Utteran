import type { APIRequestContext, Page, Request } from "@playwright/test";
import { choose, expect, SESSION, shot, test } from "./fixtures";

/**
 * UI-3 S5-A against the real FastAPI app in the `--next-ui-v1` shape (tools/ui2/serve_next_ui.py
 * --v1): real B5 store, B7 admission/token/freeze/idempotency, control store, queue CAS and
 * dispatcher, the real session/Host/Origin middleware on the real loopback authority, and the
 * browser's own `fetch(credentials: "same-origin")` (no test header is added to page requests).
 * Synthetic only: B4 evidence for `large-v3-turbo`/fw-cpu and a worker runner that loads no model
 * (tools/ui2/synthetic_v1.py). Headless Chromium — NOT a WebView2 acceptance.
 *
 * The specs share one fresh data root per run and run in file order (workers: 1): the first test
 * sees the empty store and the first-start paused queue.
 */

const ROOT = process.env.UI2_V1_ROOT!;
const input = (name: string) => `${ROOT}/in/${name}`;
const OUT = `${ROOT}/out`;
const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;
const writes = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map(line);
const legacyWrites = (seen: Request[]) => writes(seen).filter((l) => !l.includes(" /api/v1/") && !l.startsWith("PATCH /api/settings"));

async function serverJobs(request: APIRequestContext): Promise<{ id: string; status: string }[]> {
  const response = await request.get("/api/v1/jobs", { headers: AUTH });
  expect(response.status()).toBe(200);
  return ((await response.json()) as { items: { id: string; status: string }[] }).items;
}

async function addInput(page: Page, path: string): Promise<void> {
  await page.getByTestId("tx-input-path").locator("input").fill(path);
  await page.getByTestId("tx-input-add-typed").click();
}

async function setOutput(page: Page): Promise<void> {
  await page.getByTestId("v1-output-path").locator("input").fill(OUT);
}

async function createPreset(page: Page, name: string, model = "large-v3-turbo"): Promise<void> {
  await listsLoaded(page);
  await page.getByTestId("v1-preset-create-open").click();
  await page.getByTestId("v1-preset-name").locator("input").fill(name);
  await page.getByTestId("v1-preset-model").locator("input").fill(model);
  await page.getByTestId("v1-preset-check").click();
  await expect(page.getByTestId("v1-preset-confirm")).toContainText(model);
  await page.getByTestId("v1-preset-save").click();
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(name);
}

/** Both lists answered (a select opened while its list re-renders loses the click). */
async function listsLoaded(page: Page): Promise<void> {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
}

/** Select the first saved Preset/Policy (created by the first test). */
async function useSaved(page: Page, presetName = "S5-A 合成"): Promise<void> {
  await listsLoaded(page);
  await choose(page, "v1-preset-select", new RegExp(presetName));
  await expect(page.getByTestId("v1-preset-frozen")).toContainText("revision 1");
  await choose(page, "v1-policy-select", /CPU only/);
  await expect(page.getByTestId("v1-policy-frozen")).toBeVisible();
}

test("first start: empty store, nothing created implicitly; explicit Preset/Policy creation with the browser's same-origin fetch", async ({ app, request }) => {
  const seen = recordApi(app);
  await expect(app.getByTestId("v1-mode-notice")).toBeVisible();
  await expect(app.getByTestId("transcribe-not-migrated")).toHaveCount(0);
  await expect(app.getByTestId("v1-presets-empty")).toBeVisible();
  await expect(app.getByTestId("v1-policies-empty")).toBeVisible();
  await expect(app.getByTestId("v1-queue-dispatch")).toContainText(/停止中/);
  // Loading the screen and adding an input only read.
  await addInput(app, input("meeting.wav"));
  expect(writes(seen).filter((l) => l.includes("/api/v1/"))).toEqual([]);
  await expect(app.getByTestId("v1-missing")).toContainText("Preset");

  await createPreset(app, "S5-A 合成");
  await app.getByTestId("v1-policy-create-open").click();
  await app.getByTestId("v1-policy-name").locator("input").fill("CPU only");
  await app.getByTestId("v1-policy-check").click();
  await expect(app.getByTestId("v1-policy-confirm")).toBeVisible();
  await app.getByTestId("v1-policy-save").click();
  await expect(app.getByTestId("v1-policy-frozen")).toContainText("CPU only");
  expect(writes(seen).filter((l) => l.includes("/api/v1/"))).toEqual(["POST /api/v1/presets", "POST /api/v1/execution-policies"]);

  // The browser sent its own Origin and cookie; no session header was added by the page.
  for (const r of seen.filter((x) => x.method() === "POST")) {
    const headers = await r.allHeaders();
    expect(headers.origin).toBe(new URL(r.url()).origin);
    expect(headers["x-utteran-session"]).toBeUndefined();
    expect(headers["idempotency-key"]).toMatch(/^[A-Za-z0-9_.-]{16,128}$/);
  }
  const keys = await Promise.all(seen.filter((x) => x.method() === "POST").map(async (r) => (await r.allHeaders())["idempotency-key"]));
  expect(new Set(keys).size).toBe(keys.length);
  const presets = await (await request.get("/api/v1/presets", { headers: AUTH })).json();
  expect(presets.items).toHaveLength(1);
  expect(presets.items[0].settings.diarization.enabled).toBe(false);
  await shot(app, "s5a-library-created");
});

test("single job: check (validation + resolution) → submit → accepted ≠ started; queue paused until the explicit resume", async ({ app, request }) => {
  const seen = recordApi(app);
  await useSaved(app);
  await addInput(app, input("meeting.wav"));
  await setOutput(app);
  await choose(app, "v1-diff-language", /英語|English/);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toContainText(/fw-cpu/);
  await expect(app.getByTestId("v1-resolution-evidence")).toContainText("smoke");
  await expect(app.getByTestId("v1-validated")).toBeVisible();
  // The token never reaches the DOM.
  const resolution = seen.find((r) => line(r) === "POST /api/v1/execution-resolutions")!;
  const token = ((await (await resolution.response())!.json()) as { token: string }).token;
  expect(await app.content()).not.toContain(token);
  const before = (await serverJobs(request)).length;
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  await expect(app.getByTestId("v1-status")).toHaveText(/待機中/);
  await expect(app.getByTestId("v1-queue-dispatch")).toContainText(/停止中/);
  expect((await serverJobs(request)).length).toBe(before + 1);
  const body = JSON.parse(seen.find((r) => line(r) === "POST /api/v1/jobs")!.postData()!);
  expect(body.editing_session.common_overrides).toEqual([{ path: "/asr/language", value: "en" }]);
  expect(body.editing_session.files[0].input_path).toBe(input("meeting.wav"));
  expect(body.editing_session.files[0].client_file_id).toMatch(/^f-[0-9a-f]{24}$/);
  await app.waitForTimeout(1500);
  await expect(app.getByTestId("v1-status")).toHaveText(/待機中/); // nothing dispatches while paused

  await app.getByTestId("v1-queue-resume").click();
  await expect(app.getByTestId("v1-queue-dispatch")).toContainText(/実行中/);
  await expect(app.getByTestId("v1-status")).toHaveText(/完了/, { timeout: 20_000 });
  await expect(app.getByTestId("v1-job-artifacts")).toBeVisible();
  const patch = seen.find((r) => line(r) === "PATCH /api/v1/queue/control")!;
  expect((await patch.allHeaders())["if-match"]).toMatch(/^"\d+"$/);
  expect(writes(seen)).toEqual(["POST /api/v1/validations", "POST /api/v1/execution-resolutions", "POST /api/v1/jobs", "PATCH /api/v1/queue/control"]);
  expect(legacyWrites(seen)).toEqual([]);
  await shot(app, "s5a-job-succeeded");
});

test("an edit after the check makes it stale: submit is disabled until a new check", async ({ app, request }) => {
  await useSaved(app);
  await addInput(app, input("other.wav"));
  await setOutput(app);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-diff-format-vtt").click();
  await expect(app.getByTestId("v1-stale")).toBeVisible();
  await expect(app.getByTestId("v1-submit")).toBeDisabled();
  const before = (await serverJobs(request)).length;
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-stale")).toHaveCount(0);
  await expect(app.getByTestId("v1-submit")).toBeEnabled();
  expect((await serverJobs(request)).length).toBe(before);
});

test("refusals are shown with reasons and create no job: unready model (409), bad output folder (422)", async ({ app, request }) => {
  const seen = recordApi(app);
  const before = (await serverJobs(request)).length;
  await addInput(app, input("other.wav"));
  await setOutput(app);
  await createPreset(app, "S5-A unready", "small");
  await listsLoaded(app);
  await choose(app, "v1-policy-select", /CPU only/);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-rejected")).toBeVisible();
  await expect(app.getByTestId("v1-rejected-issues")).toBeVisible();
  await expect(app.getByTestId("v1-submit")).toBeDisabled();

  await useSaved(app);
  await app.getByTestId("v1-output-path").locator("input").fill(`${ROOT}/absent-folder`);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-rejected-issues")).toContainText("/files/0/output_dir");
  expect((await serverJobs(request)).length).toBe(before);
  expect(writes(seen).filter((l) => l === "POST /api/v1/jobs")).toEqual([]);
  expect(legacyWrites(seen)).toEqual([]);
});

test("lost answer (5xx after the server accepted): unknown, no automatic resend; the explicit re-check sends the same key and body and gets the same job", async ({ app, request }) => {
  const seen = recordApi(app);
  await useSaved(app);
  await addInput(app, input("meeting.wav"));
  await setOutput(app);
  let first = true;
  await app.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST" || !first) return route.continue();
    first = false;
    await route.fetch(); // the real server applies it…
    await route.fulfill({ status: 500, body: "" }); // …but the answer is lost
  });
  const before = (await serverJobs(request)).length;
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-unknown")).toBeVisible();
  await expect(app.getByTestId("v1-check")).toBeDisabled();
  await app.waitForTimeout(3000);
  const posts = () => seen.filter((r) => line(r) === "POST /api/v1/jobs");
  expect(posts()).toHaveLength(1);
  expect((await serverJobs(request)).length).toBe(before + 1);
  await app.getByTestId("v1-recheck").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  expect(posts()).toHaveLength(2);
  const [a, b] = await Promise.all(posts().map((r) => r.allHeaders()));
  expect(b!["idempotency-key"]).toBe(a!["idempotency-key"]);
  expect(posts()[1]!.postData()).toBe(posts()[0]!.postData());
  expect((await serverJobs(request)).length).toBe(before + 1);
});

test("cooperative cancel: requested first, cancelled only when GET says so; no force", async ({ app }) => {
  const seen = recordApi(app);
  await useSaved(app);
  await addInput(app, input("slow-meeting.wav"));
  await setOutput(app);
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-status")).toHaveText(/実行中/, { timeout: 20_000 });
  // Cancel against the settled running revision: a cancel sent while the dispatcher still moves
  // dispatch_started → launch_confirmed is (correctly) a 412 that is re-read, never resent.
  await expect(app.getByTestId("v1-job-reason")).toContainText("起動を確認", { timeout: 20_000 });
  await app.getByTestId("v1-cancel").click();
  await expect(app.getByTestId("v1-cancel-state")).toContainText(/取消を要求しました|ジョブは終了しています/);
  await expect(app.getByTestId("v1-cancel-state")).toBeVisible();
  await expect(app.getByTestId("v1-status")).toHaveText(/取消済み/, { timeout: 20_000 });
  const cancel = seen.find((r) => line(r).endsWith("/cancel"))!;
  expect(JSON.parse(cancel.postData()!)).toEqual({ schema_version: 1, mode: "cooperative" });
  expect((await cancel.allHeaders())["if-match"]).toMatch(/^"\d+"$/);
});

test("Expert uses the same v1 controller; multi-file start is shown disabled; a reload re-reads the durable jobs only", async ({ app }) => {
  const seen = recordApi(app);
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("v1-batch-start")).toBeDisabled();
  await expect(app.getByTestId("tx-batch-start")).toHaveCount(0);
  await expect(app.getByTestId("tx-start")).toHaveCount(0);
  // One v1 job panel (the shared controller), in the third pane.
  await expect(app.getByTestId("pane-queue").getByTestId("v1-job")).toHaveCount(1);
  await expect(app.getByTestId("v1-recent-items")).toBeVisible();
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await expect(app.getByTestId("v1-unknown")).toHaveCount(0); // page memory only
  await expect(app.getByTestId("v1-recent-item").first()).toBeVisible();
  expect(writes(seen)).toEqual([]);
});

test("auth on the real loopback authority: no cookie 401, foreign Origin 403, cookie-only write without Origin 403", async ({ app, request, baseURL }) => {
  const status = await app.evaluate(async () => (await fetch("/api/v1/presets", { credentials: "omit" })).status);
  expect(status).toBe(401);
  const foreign = await request.post("/api/v1/presets", { headers: { ...AUTH, Origin: "http://127.0.0.1:1", "Idempotency-Key": "e2e-s5a-foreign-0001" }, data: {} });
  expect(foreign.status()).toBe(403);
  const noOrigin = await request.post("/api/v1/presets", { headers: { ...AUTH, "Idempotency-Key": "e2e-s5a-noorigin-001" }, data: {} });
  expect(noOrigin.status()).toBe(403);
  expect((await noOrigin.json()).error.code).toBe("origin_required");
  // The legacy start stays closed on the server too (409), and the page never called it.
  const legacy = await request.post("/api/jobs", { headers: AUTH, data: {} });
  expect([409, 422]).toContain(legacy.status());
  expect(baseURL).toContain("127.0.0.1");
});
