import { writeFileSync } from "node:fs";
import type { APIRequestContext, Page } from "@playwright/test";
import { expect, SESSION } from "./fixtures";
import { test } from "./isolatedV1";

/**
 * 束F against the real FastAPI app in the `--next-ui-v1` shape with `--setup-e2e`: real B8 store,
 * planner, executor (journal, heavy slot shared with B7, cancel/retry/resume), setup routes,
 * diagnostics service (single-flight, one retry after a timeout) and readiness check, real B5/B7.
 * Synthetic only (tools/ui2/synthetic_setup.py): the profile CLI, the step children, the OS/runtime
 * probes and the B4 evidence of the verification step. Headless Chromium — NOT a WebView2,
 * GPU, real-model or real-VC++ acceptance.
 */
test.use({ setupE2e: true });

const AUTH = { Cookie: `utteran_gui_session=${SESSION}` };
const trigger = (root: string, name: string) => writeFileSync(`${root}/setup/${name}`, "x");

async function openSetup(app: Page): Promise<void> {
  await app.getByTestId("nav-setup").click();
  await expect(app.getByTestId("fsetup")).toBeVisible();
}

async function detect(app: Page): Promise<void> {
  await app.getByTestId("fsetup-detect").click();
  await expect(app.getByTestId("fsetup-recommendation")).toBeVisible();
  await expect(app.getByTestId("fsetup-detecting")).toHaveCount(0);
}

/** v1 starts with dispatch paused (never resumed automatically): the person resumes it. */
async function startPlan(app: Page): Promise<void> {
  await app.getByTestId("fsetup-start").click();
  await expect(app.getByTestId("fsetup-operation")).toBeVisible();
  const paused = app.getByTestId("fsetup-queue-paused");
  const moving = app.locator('[data-testid="fsetup-operation"]:not([data-status="queued"])');
  await expect(paused.or(moving)).toBeVisible();
  if (await paused.isVisible()) await app.getByTestId("fsetup-queue-resume").click();
}

async function runToEnd(app: Page, status = "succeeded"): Promise<void> {
  await expect(app.getByTestId("fsetup-operation")).toHaveAttribute("data-status", status, { timeout: 20_000 });
}

async function post(request: APIRequestContext, url: string, path: string, data: object, key: string) {
  return request.post(path, { headers: { ...AUTH, Origin: url, "Idempotency-Key": key }, data });
}

test("F-01 standard GPU setup on a clean root: plan → confirm → progress → defaults → ready → B7 admission", async ({ app, backend, request }) => {
  const writes: string[] = [];
  app.on("request", (r) => {
    const { pathname } = new URL(r.url());
    if (pathname.startsWith("/api/v1/setup") || pathname.startsWith("/api/v1/readiness")) writes.push(`${r.method()} ${pathname}`);
  });
  await openSetup(app);
  // Opening the page only reads (GET); nothing is probed until the person asks.
  expect(writes.every((w) => w.startsWith("GET "))).toBe(true);
  await detect(app);
  await expect(app.getByTestId("fsetup-adapter-0")).toContainText("NVIDIA Synthetic GPU");
  await expect(app.getByTestId("fsetup-vcredist")).toContainText("導入済み");
  await expect(app.getByTestId("fsetup-standard-summary")).toContainText("NVIDIA CUDA");
  await expect(app.getByTestId("fsetup-standard-summary")).toContainText("large-v3-turbo");
  await expect(app.getByTestId("fsetup-runtime-reason")).toContainText("CUDA 用の実行環境がまだない");
  await app.getByTestId("fsetup-plan").click();
  await expect(app.getByTestId("fsetup-review")).toBeVisible();
  await expect(app.getByTestId("fsetup-changes")).toContainText("実行環境 cuda を新しく作成");
  await expect(app.getByTestId("fsetup-estimate")).toContainText("不明");
  await expect(app.getByTestId("fsetup-steps")).toContainText("実行の検証");
  // Nothing ran yet: no operation exists before the explicit start.
  const before = await request.get("/api/v1/setup-operations", { headers: AUTH });
  expect(((await before.json()) as { items: unknown[] }).items).toEqual([]);
  await expect(app.getByTestId("fsetup-start")).toBeEnabled();
  await app.getByTestId("fsetup-start").click();
  await expect(app.getByTestId("fsetup-queue-paused")).toBeVisible();
  await app.getByTestId("fsetup-queue-resume").click();
  await runToEnd(app);
  await expect(app.getByTestId("fsetup-op-steps").locator('[data-step="capability_smoke"]')).toHaveAttribute("data-status", "succeeded");
  await expect(app.getByTestId("fsetup-defaults-created")).toBeVisible();
  await expect(app.getByTestId("fsetup-ready-yes")).toContainText("fw-cuda");
  // The defaults are a real B5 Preset/Policy; a job with them is admitted by the real B7 path.
  const presets = (await (await request.get("/api/v1/presets", { headers: AUTH })).json()) as { items: { id: string; revision: number }[] };
  const policies = (await (await request.get("/api/v1/execution-policies", { headers: AUTH })).json()) as { items: { id: string; revision: number }[] };
  expect(presets.items).toHaveLength(1);
  expect(policies.items).toHaveLength(1);
  const editing = {
    schema_version: 1,
    preset_ref: { id: presets.items[0]!.id, revision: presets.items[0]!.revision },
    policy_ref: { id: policies.items[0]!.id, revision: policies.items[0]!.revision },
    common_overrides: [],
    files: [{ client_file_id: "f-fsetup0000000000000000000", input_path: `${backend.root}/in/meeting.wav`, output_dir: `${backend.root}/out`, overrides: [] }],
  };
  const resolution = await post(request, backend.url, "/api/v1/execution-resolutions", editing, "fsetup-e2e-resolve-0001");
  expect(resolution.status()).toBe(201);
  const token = ((await resolution.json()) as { token: string }).token;
  const job = await post(request, backend.url, "/api/v1/jobs", { schema_version: 1, editing_session: editing, resolution_token: token, resume_mode: "resume" }, "fsetup-e2e-job-00000001");
  expect(job.status()).toBe(202);
});

test("F-02 detection: the first runtime probe times out, the retry answers; VC++ missing is shown with the official source", async ({ app, backend }) => {
  // CUDA is judged from the cuda profile only (a CPU venv never answers for the GPU).
  trigger(backend.root, "profile-cuda");
  trigger(backend.root, "runtime-timeout");
  trigger(backend.root, "vcredist-missing");
  await openSetup(app);
  await detect(app);
  await expect(app.getByTestId("fsetup-runtime")).toContainText("利用可");
  await expect(app.getByTestId("fsetup-runtime-retried")).toBeVisible();
  await expect(app.getByTestId("fsetup-vcredist-missing")).toContainText("aka.ms/vs/17/release/vc_redist.x64.exe");
  await app.getByTestId("fsetup-redetect").click();
  await expect(app.getByTestId("fsetup-runtime-retried")).toHaveCount(0);
});

test("F-03 custom CPU: failed verification → retry succeeds; an additional setup keeps the existing defaults", async ({ app, backend }) => {
  trigger(backend.root, "fail-verify");
  await openSetup(app);
  await detect(app);
  await app.getByTestId("fsetup-mode-custom").click();
  await app.getByTestId("fsetup-profile").click();
  await app.getByRole("option", { name: /Intel/ }).click();
  await expect(app.getByTestId("gsetup-variant")).toContainText("vulkan");
  await expect(app.getByTestId("fsetup-model-base").locator("input")).toBeChecked();
  await app.getByTestId("fsetup-profile").click();
  await app.getByRole("option", { name: /^CPU/ }).click();
  await app.getByTestId("fsetup-model-small").locator("input").check();
  await app.getByTestId("fsetup-plan").click();
  await expect(app.getByTestId("fsetup-changes")).toContainText("small");
  await startPlan(app);
  await runToEnd(app, "failed");
  await expect(app.getByTestId("fsetup-op-steps").locator('[data-step="capability_smoke"]')).toContainText("ドライバー");
  await app.getByTestId("fsetup-retry").click();
  await runToEnd(app);
  await expect(app.getByTestId("fsetup-defaults-created")).toBeVisible();
  await expect(app.getByTestId("fsetup-ready-yes")).toContainText("fw-cpu");
  // A second, additional setup never rewrites the defaults.
  await app.getByTestId("fsetup-dismiss").click();
  await app.getByTestId("fsetup-mode-custom").click();
  await app.getByTestId("fsetup-model-large-v3").locator("input").check();
  await app.getByTestId("fsetup-plan").click();
  await startPlan(app);
  await runToEnd(app);
  await expect(app.getByTestId("fsetup-defaults-kept")).toBeVisible();
});

test("F-04 reload during a running setup recovers its progress; cooperative cancel ends it", async ({ app, backend }) => {
  trigger(backend.root, "hold-download");
  await openSetup(app);
  await detect(app);
  await app.getByTestId("fsetup-plan").click();
  await startPlan(app);
  await expect(app.getByTestId("fsetup-operation")).toHaveAttribute("data-status", "running", { timeout: 20_000 });
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await openSetup(app);
  await expect(app.getByTestId("fsetup-operation")).toHaveAttribute("data-status", /running|cancelling/);
  await app.getByTestId("fsetup-cancel").click();
  await expect(app.getByTestId("fsetup-operation")).toHaveAttribute("data-status", "cancelling");
  // The running child is never killed: it stops at the next step boundary.
  const { unlinkSync } = await import("node:fs");
  unlinkSync(`${backend.root}/setup/hold-download`);
  await runToEnd(app, "cancelled");
  await expect(app.getByTestId("fsetup-retry")).toBeVisible();
});

test("F-05 diarization needs a token and the agreement; without them nothing can start", async ({ app }) => {
  await openSetup(app);
  await detect(app);
  await app.getByTestId("fsetup-diarization").locator("input").check();
  await expect(app.getByTestId("fsetup-token-state")).toContainText("未登録");
  await app.getByTestId("fsetup-plan").click();
  await expect(app.getByTestId("fsetup-credential-missing")).toBeVisible();
  await expect(app.getByTestId("fsetup-agreement-missing")).toBeVisible();
  await expect(app.getByTestId("fsetup-start")).toBeDisabled();
});

test("F-06 a first setup that succeeded while the setup page was closed still gets its defaults (server, once)", async ({ app, backend, request }) => {
  trigger(backend.root, "hold-download");
  await openSetup(app);
  await detect(app);
  await app.getByTestId("fsetup-plan").click();
  await startPlan(app);
  await expect(app.getByTestId("fsetup-operation")).toHaveAttribute("data-status", "running", { timeout: 20_000 });
  // Unload the app while the child is still held. Section navigation keeps the
  // AppCore polling, so it cannot simulate completing with the GUI closed.
  const appUrl = app.url();
  await app.goto("about:blank");
  const { unlinkSync } = await import("node:fs");
  unlinkSync(`${backend.root}/setup/hold-download`);
  await expect.poll(async () => {
    const list = (await (await request.get("/api/v1/setup-operations", { headers: AUTH })).json()) as { items: { status: string }[] };
    return list.items[0]?.status;
  }, { timeout: 20_000 }).toBe("succeeded");
  expect(((await (await request.get("/api/v1/presets", { headers: AUTH })).json()) as { items: unknown[] }).items).toHaveLength(0);
  await app.goto(appUrl);
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await openSetup(app);
  await expect(app.getByTestId("fsetup-defaults-created")).toBeVisible();
  await expect(app.getByTestId("fsetup-ready-yes")).toBeVisible();
  await app.reload();
  await expect(app.getByTestId("app-ready")).toBeVisible();
  await openSetup(app);
  await expect(app.getByTestId("fsetup-defaults-created")).toHaveCount(0); // read only, nothing more
  expect(((await (await request.get("/api/v1/presets", { headers: AUTH })).json()) as { items: unknown[] }).items).toHaveLength(1);
});
