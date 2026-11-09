import { readFileSync } from "node:fs";
import type { Page, Request } from "@playwright/test";
import { choose, expect, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * UI-3 S2 against the real FastAPI app: GET /api/environment is answered by the *real*
 * EnvironmentService (active-profile rule, derive_options, error masking) on top of a synthetic
 * profile CLI (tools/ui2/synthetic_environment.py): cpu = CPU only / no model, cuda = one CUDA GPU
 * with models (answers after 1.5 s), intel = XPU + OpenVINO + native build, needs a rebuild and one
 * CLI call fails (partial), vulkan = not built. No real GPU, profile or path is used.
 * Headless Chromium — NOT a WebView2 acceptance.
 */

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const envRequests = (seen: Request[]) => seen.filter((r) => new URL(r.url()).pathname === "/api/environment").map((r) => new URL(r.url()).search);
const writes = (seen: Request[]) => seen.filter((r) => r.method() !== "GET").map((r) => `${r.method()} ${new URL(r.url()).pathname}`);
const sum = (page: Page, item: string) => page.getByTestId(`env-sum-${item}`).locator("dd");

/** Page text without the one sentence that explicitly denies readiness ("…（ready）ではありません"). */
async function bodyText(page: Page): Promise<string> {
  const text = await page.evaluate(() => document.body.innerText);
  return text.replace(/（ready）ではありません|\(not "ready"\)/g, "");
}

test("normal: the four summary items follow the real answer; nothing obtained is 'not obtained'", async ({ app }) => {
  // Start-up (no saved default, several built): the server chose none.
  await expect(app.getByTestId("env-note")).toContainText("どれも選びませんでした");
  await expect(sum(app, "hardware")).toHaveText("未取得");
  await expect(sum(app, "profile")).toHaveText("なし");
  await expect(sum(app, "models")).toHaveText("未取得");
  await expect(sum(app, "native")).toHaveText("未取得");

  await choose(app, "env-profile", /^cpu/);
  await expect(sum(app, "profile")).toHaveText("cpu");
  await expect(sum(app, "hardware")).toHaveText("CPU · 8スレッド");
  await expect(sum(app, "models")).toHaveText("0件");
  await expect(sum(app, "native")).toHaveText("なし");
  await expect(app.getByTestId("env-note")).toHaveCount(0);

  await choose(app, "env-profile", /^cuda/);
  await expect(sum(app, "profile")).toHaveText("cuda");
  await expect(sum(app, "hardware")).toHaveText("Synthetic CUDA GPU 0");
  await expect(sum(app, "models")).toHaveText("3件"); // installed rows, not the 2 candidate models
  await expect(app.getByTestId("env-readiness")).toHaveText("検出情報・実行可否は未検証");
  await shot(app, "s2-01-normal-cuda");

  // Normal keeps details folded; opening them shows the read-only candidates.
  await app.getByTestId("env-more").getByRole("button", { name: "詳細を表示" }).click();
  await app.getByTestId("env-detail-candidates").getByRole("button").first().click();
  await expect(app.getByTestId("env-asr")).toContainText("faster-whisper — モデル 2件 / デバイス: CPU, Synthetic CUDA GPU 0");
  await expect(app.getByTestId("env-diarization")).toContainText("pyannote.audio");
  const text = await bodyText(app);
  expect(text).not.toMatch(/\bready\b|GPU実行可|推奨/i);
});

test("expert: details per category from the same snapshot; mode switches add no request", async ({ app }) => {
  const seen = recordApi(app);
  await choose(app, "env-profile", /^intel/);
  await expect(sum(app, "profile")).toHaveText("intel");
  expect(envRequests(seen)).toEqual(["?profile=intel"]);

  await app.getByTestId("mode-expert").click();
  const pane = app.getByTestId("pane-inspector");
  await expect(pane.getByTestId("env-details")).toBeVisible();
  await expect(pane.getByTestId("env-profile-row-intel")).toContainText("構築: 構築済み / 互換性: 現行版と不一致");
  await expect(pane.getByTestId("env-profile-row-intel")).toContainText("依存環境が現行版と一致しません");
  await expect(pane.getByTestId("env-profile-row-intel")).toContainText("サーバーが使用");
  await expect(pane.getByTestId("env-profile-row-intel")).toContainText("この画面で選択");
  await expect(pane.getByTestId("env-profile-row-cuda")).toContainText("構築: 構築済み / 互換性: 不明");
  await expect(pane.getByTestId("env-profile-row-vulkan")).toContainText("構築: 未構築");
  await expect(pane.getByTestId("env-accelerators")).toContainText("PyTorch XPU");
  await expect(pane.getByTestId("env-openvino-devices")).toContainText("CPU, GPU.0, NPU");
  await expect(pane.getByTestId("env-probes")).toContainText("vulkan: 時間切れ");
  await expect(pane.getByTestId("env-installed-models")).toContainText("Synthetic large-v3-turbo-q5_0");
  await expect(pane.getByTestId("env-openvino-ir")).toContainText("生成済み 1 / 2");
  await expect(pane.getByTestId("env-native")).toContainText("記録上のvariant: cpu, openvino");
  await expect(pane.getByTestId("env-native")).toContainText("動作確認済みであることを意味しません");
  await expect(pane.getByTestId("env-asr")).toContainText("whisper.cpp");
  // Partial: the failed call is a fixed message; the server's text (with its path) is not shown.
  await expect(pane.getByTestId("env-partial")).toContainText("一部の情報を取得できませんでした（1件）");
  await expect(pane.getByTestId("env-issues")).toHaveText("一部の情報を取得できませんでした。");
  await expect(pane.getByTestId("env-rebuild")).toContainText("「intel」は再構築が必要です");
  // An HTML-looking device name is text, not markup.
  await expect(pane.getByTestId("env-accelerators")).toContainText("<b>XPU</b>");
  await expect(pane.getByTestId("env-accelerators").locator("b")).toHaveCount(0);
  // Candidates are listed, not offered as actions.
  await expect(pane.getByTestId("env-detail-candidates").locator("input, select, [role=option], [role=checkbox]")).toHaveCount(0);
  await shot(app, "s2-02-expert-intel");

  const text = await bodyText(app);
  expect(text).not.toContain("synthetic-root");
  expect(text).not.toContain("CLI exited");
  expect(text).not.toMatch(/\bready\b|GPU実行可|推奨/i);

  await app.getByTestId("mode-normal").click();
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-settings").click();
  await app.getByTestId("nav-transcribe").click();
  await expect(pane.getByTestId("env-active")).toContainText("intel");
  expect(envRequests(seen)).toEqual(["?profile=intel"]);
  expect(writes(seen)).toEqual([]);
  expect(seen.filter((r) => r.url().includes("/api/v1"))).toEqual([]);
});

test("refresh=true only for re-detect; a double click starts one probe; nothing is written", async ({ app }) => {
  const seen = recordApi(app);
  await choose(app, "env-profile", /^cpu/);
  await expect(sum(app, "profile")).toHaveText("cpu");
  await app.getByTestId("env-redetect").dblclick();
  await expect(app.getByTestId("env-loading")).toBeVisible();
  await expect(app.getByTestId("env-redetect")).toBeDisabled();
  await expect(app.getByTestId("env-loading")).toHaveCount(0);
  await app.getByTestId("nav-settings").click();
  await app.getByTestId("nav-transcribe").click();
  expect(envRequests(seen)).toEqual(["?profile=cpu", "?profile=cpu&refresh=true"]);
  expect(writes(seen)).toEqual([]);
});

test("slow CUDA → fast CPU → late CUDA keeps CPU; cancel discards the late answer and labels the old one", async ({ app }) => {
  await choose(app, "env-profile", /^cuda/);
  await choose(app, "env-profile", /^cpu/);
  await expect(sum(app, "profile")).toHaveText("cpu");
  await app.waitForTimeout(2000); // the stale cuda answer arrives meanwhile
  await expect(sum(app, "profile")).toHaveText("cpu");
  await expect(app.getByTestId("env-profile")).toContainText("cpu");

  await choose(app, "env-profile", /^cuda/);
  await expect(app.getByTestId("env-elapsed")).toContainText("経過");
  await expect(app.getByTestId("env-previous")).toContainText("「cpu」について");
  await app.getByTestId("env-cancel").click();
  await expect(app.getByTestId("env-cancelled")).toContainText("サーバー側の検出処理は止まらず");
  await app.waitForTimeout(2000);
  await expect(app.getByTestId("env-cancelled")).toBeVisible();
  await expect(sum(app, "profile")).toHaveText("cpu"); // still the previous result, still labelled
  await expect(app.getByTestId("env-previous")).toBeVisible();
  await expect(app.getByTestId("env-active")).toHaveCount(0);
  await shot(app, "s2-03-cancelled");
  await app.getByTestId("env-cancelled-retry").click();
  await expect(sum(app, "profile")).toHaveText("cuda");
  await expect(app.getByTestId("env-previous")).toHaveCount(0);
});

test("while detecting and after a failure, settings and mode switches stay usable; retry recovers", async ({ app }) => {
  let failures = 1;
  await app.route("**/api/environment*", async (route) => {
    if (failures-- > 0) return route.abort("timedout");
    return route.continue();
  });
  await choose(app, "env-profile", /^cuda/);
  await expect(app.getByTestId("env-error")).toBeVisible();
  await expect(app.getByTestId("env-previous")).toBeVisible();
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-page")).toBeVisible();
  await choose(app, "settings-theme", "ダーク");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("nav-transcribe").click();
  await app.getByTestId("env-error-retry").click();
  await expect(app.getByTestId("env-loading")).toBeVisible();
  // Mode switch and navigation during the 1.5 s probe.
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("pane-inspector").getByTestId("env-loading")).toBeVisible();
  await app.getByTestId("mode-normal").click();
  await expect(sum(app, "profile")).toHaveText("cuda");
  await expect(app.getByTestId("env-error")).toHaveCount(0);
});

test("no built profile, an unbuilt request and an empty answer are states, not errors", async ({ app }) => {
  await app.route("**/api/environment*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    // Locally rewrite the real answer to "nothing built at all".
    body.profiles = (body.profiles as Record<string, unknown>[]).map((p) => ({ ...p, exists: false }));
    body.active_profile = null;
    body.profile_warnings = [];
    return route.fulfill({ response, json: body });
  });
  await app.getByTestId("env-redetect").click();
  await expect(app.getByTestId("env-note")).toContainText("使用できるプロファイルがありません");
  await expect(app.getByTestId("env-error")).toHaveCount(0);
  await app.getByTestId("env-profile").click();
  await expect(app.getByRole("option", { name: /^cuda — 未構築/ })).toHaveClass(/v-list-item--disabled/);
  await app.keyboard.press("Escape");
});

test("the server's fallback for a not-built default is shown; the saved default is not rewritten", async ({ app, request }) => {
  const seen = recordApi(app);
  // Save "vulkan" (not built) as the default through the real S1 page.
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-profile", "Vulkan");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("env-profile")).toContainText("既定に従う（vulkan）");
  await expect(app.getByTestId("env-note")).toContainText("「vulkan」は使用できません");
  const stored = await request.get("/api/settings", { headers: SESSION_HEADER });
  expect((await stored.json()).default_profile).toBe("vulkan");
  // A manual choice is kept when the default is saved again.
  await choose(app, "env-profile", /^cpu/);
  await expect(sum(app, "profile")).toHaveText("cpu");
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-profile", "CUDA");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("nav-transcribe").click();
  await expect(sum(app, "profile")).toHaveText("cpu");
  // Following the default again uses the saved value.
  await choose(app, "env-profile", /^既定に従う/);
  await expect(sum(app, "profile")).toHaveText("cuda");
  expect(writes(seen)).toEqual(["PATCH /api/settings", "PATCH /api/settings"]);
  expect(envRequests(seen)).toEqual(["?profile=vulkan", "?profile=cpu", "?profile=cuda"]);
});

test("900×660, English, long names: no horizontal scroll, keyboard reaches the profile selector", async ({ app }) => {
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-language", "English");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("Saved.");
  await app.getByTestId("nav-transcribe").click();
  await app.setViewportSize({ width: 900, height: 660 });
  await choose(app, "env-profile", /^intel/);
  await expect(sum(app, "hardware")).toContainText("deliberately very long device name");
  await expect(app.getByTestId("env-readiness")).toHaveText("Detected; whether it runs is unverified");
  await app.getByTestId("mode-expert").click();
  const pane = app.getByTestId("pane-inspector");
  await expect(pane.getByTestId("env-partial")).toContainText("Some information could not be obtained (1)");
  await expect(pane.getByTestId("env-rebuild")).toContainText("needs a rebuild (dependencies differ from this version)");
  const overflow = await app.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(app, "s2-04-narrow-en-expert");
  await app.getByTestId("mode-normal").click();
  let onSelector = false;
  for (let i = 0; i < 40 && !onSelector; i += 1) {
    await app.keyboard.press("Tab");
    onSelector = await app.evaluate(() => Boolean(document.activeElement?.closest('[data-testid="env-profile"]')));
  }
  expect(onSelector).toBe(true);
});

// ---- PR #41 review fixes -------------------------------------------------------------------

const snapshots = JSON.parse(readFileSync(new URL("../src/test/fixtures/environment-snapshots.json", import.meta.url), "utf-8")) as Record<string, unknown>;
const roles = (page: Page, profile: string) => page.getByTestId("pane-inspector").getByTestId(`env-profile-row-${profile}`).locator("[data-role]");

/**
 * One route handler for `profile` for the whole test (never unrouted while a request is held,
 * which would complete the Route behind the handler's back). Each request waits until the test
 * decides its outcome with `next()`; after `passthrough()` requests go straight to the server.
 */
async function gateProfile(page: Page, profile: string) {
  const waiting: ((outcome: "continue" | "abort") => void)[] = [];
  let passthrough = false;
  await page.route(`**/api/environment?profile=${profile}*`, async (route) => {
    if (passthrough) return route.continue();
    const outcome = await new Promise<"continue" | "abort">((resolve) => waiting.push(resolve));
    // A request the page itself already aborted (cancel) cannot be continued; that is expected.
    await (outcome === "abort" ? route.abort("connectionreset") : route.continue()).catch(() => undefined);
  });
  return {
    async next(outcome: "continue" | "abort") {
      await expect.poll(() => waiting.length).toBeGreaterThan(0);
      waiting.shift()!(outcome);
    },
    passthrough() {
      passthrough = true;
    },
  };
}

test("P2-1: while CUDA is pending, failed or cancelled, the CPU answer carries no CUDA/current roles", async ({ app }) => {
  const seen = recordApi(app);
  await choose(app, "env-profile", /^cpu/);
  await expect(sum(app, "profile")).toHaveText("cpu");
  await app.getByTestId("mode-expert").click();
  await expect(roles(app, "cpu")).toHaveText(["サーバーが使用", "この画面で選択"]);

  // Pending.
  const cuda = await gateProfile(app, "cuda");
  await choose(app, "env-profile", /^cuda/);
  await expect(app.getByTestId("pane-inspector").getByTestId("env-previous")).toBeVisible();
  await expect(roles(app, "cuda")).toHaveCount(0);
  await expect(roles(app, "cpu")).toHaveText(["前回の取得時にサーバーが使用"]);
  // Error.
  await cuda.next("abort");
  await expect(app.getByTestId("pane-inspector").getByTestId("env-error")).toBeVisible();
  await expect(roles(app, "cuda")).toHaveCount(0);
  await expect(roles(app, "cpu")).toHaveText(["前回の取得時にサーバーが使用"]);
  // Cancelled (the retry is held, then the wait is stopped).
  await app.getByTestId("pane-inspector").getByTestId("env-error-retry").click();
  await expect(app.getByTestId("pane-inspector").getByTestId("env-loading")).toBeVisible();
  await app.getByTestId("pane-inspector").getByTestId("env-cancel").click();
  await expect(app.getByTestId("pane-inspector").getByTestId("env-cancelled")).toBeVisible();
  await expect(roles(app, "cuda")).toHaveCount(0);
  await expect(roles(app, "cpu")).toHaveText(["前回の取得時にサーバーが使用"]);
  await cuda.next("continue"); // the late answer of the cancelled request is discarded
  await expect(app.getByTestId("pane-inspector").getByTestId("env-cancelled")).toBeVisible();
  await expect(roles(app, "cuda")).toHaveCount(0);
  // A successful retry shows the current roles again.
  cuda.passthrough();
  await app.getByTestId("pane-inspector").getByTestId("env-cancelled-retry").click();
  await expect(roles(app, "cuda")).toHaveText(["サーバーが使用", "この画面で選択"]);
  await expect(roles(app, "cpu")).toHaveCount(0);
  // Mode switches add no request; nothing was written.
  await app.getByTestId("mode-normal").click();
  await app.getByTestId("mode-expert").click();
  expect(envRequests(seen)).toEqual(["?profile=cpu", "?profile=cuda", "?profile=cuda", "?profile=cuda"]);
  expect(writes(seen)).toEqual([]);
});

test("P2-1: a default saved while the old answer is shown is not attached to it", async ({ app }) => {
  // Follow a saved default "cpu" first.
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-profile", "CPU");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click();
  await expect(roles(app, "cpu")).toHaveText(["サーバーが使用", "取得時の設定の既定"]);
  // Save "cuda" while its answer is held: the shown cpu answer must not get the new default.
  const cuda = await gateProfile(app, "cuda");
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-profile", "CUDA");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("pane-inspector").getByTestId("env-loading")).toBeVisible();
  await expect(roles(app, "cuda")).toHaveCount(0);
  await expect(roles(app, "cpu")).toHaveText(["前回の取得時にサーバーが使用"]);
  await cuda.next("continue");
  await expect(roles(app, "cuda")).toHaveText(["サーバーが使用", "取得時の設定の既定"]);
});

test("P2-2: a failed model list is 'unknown', not 0; a missing native variant report is 'unknown'", async ({ app }) => {
  await app.route("**/api/environment?profile=cuda*", async (route) => {
    const response = await route.fetch(); // real request through the session; body replaced by the real-service fixture
    return route.fulfill({ response, json: snapshots.cuda_models_list_failed });
  });
  await choose(app, "env-profile", /^cuda/);
  await expect(sum(app, "profile")).toHaveText("cuda");
  await expect(sum(app, "models")).toHaveText("不明");
  await expect(app.getByTestId("env-partial")).toBeVisible();
  await app.getByTestId("mode-expert").click();
  const pane = app.getByTestId("pane-inspector");
  await expect(pane.getByTestId("env-sum-models").locator("dd")).toHaveText("不明");
  await expect(pane.getByTestId("env-detail-models")).toContainText("0件とは限りません");

  await app.route("**/api/environment?profile=intel*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, Record<string, Record<string, unknown>>>;
    delete body.devices!.native!.variants;
    return route.fulfill({ response, json: body });
  });
  await choose(app, "env-profile", /^intel/);
  await expect(pane.getByTestId("env-sum-native").locator("dd")).toHaveText("不明");
  await expect(pane.getByTestId("env-native")).toContainText("記録上のvariant: 不明");
  await app.getByTestId("mode-normal").click();
  await expect(sum(app, "native")).toHaveText("不明");
  await expect(sum(app, "models")).toHaveText("1件"); // intel's model list itself was fine
});
