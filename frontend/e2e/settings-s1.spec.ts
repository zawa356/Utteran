import type { Page, Request } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER, shot, test } from "./fixtures";

/**
 * UI-3 S1 against the real FastAPI app (tools/ui2/serve_next_ui.py): GET/PATCH /api/settings,
 * GET/PUT/DELETE /api/token through the real TokenStore on an in-memory synthetic keyring,
 * GET /api/wizard/status and POST /api/open-folder. Headless Chromium — NOT a WebView2 acceptance;
 * the native folder dialog is replaced by a scripted `window.pywebview.api` so the production
 * pywebview adapter is exercised without opening an OS dialog.
 */

// Assembled at runtime: no token-shaped literal is committed (AGENTS.md privacy rule).
const SECRET = ["hf", "synthetic", "e2e", "value", "0001"].join("_");
const REJECTED = ["synthetic", "keyring", "reject", "0002"].join("-");
// Written by the synthetic keyring, but its read-back fails → real TokenStore raises → 503.
const READBACK = ["synthetic", "keyring", "readback", "fail", "0003"].join("-");
const OTHER = ["hf", "synthetic", "e2e", "value", "0004"].join("_");

declare global {
  interface Window {
    __picks: string[];
    __nextPick: string | null;
  }
}

/** Reload the app with a scripted native bridge (choose_path answers `__nextPick`). */
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

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}

const folderInput = (page: Page, which: "input" | "output") => page.getByTestId(`settings-${which}-dir-input`).locator("input");

test("defaults: profile and both folders are saved via real PATCH, survive reload and match the legacy GUI", async ({ app, request }) => {
  await withNativeBridge(app);
  // Wizard progress written by the legacy wizard must survive partial PATCHes.
  expect((await request.post("/api/wizard/start", { headers: SESSION_HEADER })).status()).toBe(200);
  const before = await (await request.get("/api/wizard/status", { headers: SESSION_HEADER })).json();
  const calls = recordApi(app);
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-profile")).toContainText("指定なし");
  await choose(app, "settings-profile", "CUDA");
  await folderInput(app, "input").fill("synthetic-in");
  await app.evaluate(() => (window.__nextPick = "synthetic-picked-out"));
  await app.getByTestId("settings-output-dir-pick").click();
  await expect(folderInput(app, "output")).toHaveValue("synthetic-picked-out");
  expect(await app.evaluate(() => window.__picks)).toEqual(["output_folder"]);
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  await app.getByTestId("settings-save").dblclick();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  const patches = calls.filter((r) => r.method() === "PATCH").map((r) => r.postDataJSON());
  expect(patches).toEqual([{ default_profile: "cuda", default_input_dir: "synthetic-in", default_output_dir: "synthetic-picked-out" }]);
  expect(calls.some((r) => r.method() === "PUT" && r.url().endsWith("/api/settings"))).toBe(false);

  const stored = await (await request.get("/api/settings", { headers: SESSION_HEADER })).json();
  expect(stored).toMatchObject({ default_profile: "cuda", default_input_dir: "synthetic-in", default_output_dir: "synthetic-picked-out", theme: "system" });
  const after = await (await request.get("/api/wizard/status", { headers: SESSION_HEADER })).json();
  expect(after.started_at).toBe(before.started_at);
  expect(after.step).toBe(before.step);
  await shot(app, "s1-01-defaults-saved");

  // Clearing: "not specified" is sent as null, an empty folder as "".
  await choose(app, "settings-profile", "指定なし");
  await app.getByTestId("settings-input-dir-clear").click();
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  expect(calls.filter((r) => r.method() === "PATCH").map((r) => r.postDataJSON())[1]).toEqual({ default_profile: null, default_input_dir: "" });
  await choose(app, "settings-profile", "Intel");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");

  await app.reload();
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-profile")).toContainText("Intel");
  await expect(folderInput(app, "input")).toHaveValue("");
  await expect(folderInput(app, "output")).toHaveValue("synthetic-picked-out");

  // The legacy GUI reads the same file.
  await app.goto("/");
  await expect(app.locator("#default-output")).toHaveValue("synthetic-picked-out");
  await expect(app.locator("#default-input")).toHaveValue("");
});

test("folder chooser: cancel keeps the value; revert restores; a plain browser explains instead of hanging", async ({ app }) => {
  await withNativeBridge(app);
  await app.getByTestId("nav-settings").click();
  await folderInput(app, "input").fill("typed-in");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("保存しました。");
  await app.getByTestId("settings-input-dir-pick").click(); // __nextPick = null → cancel
  await expect(folderInput(app, "input")).toHaveValue("typed-in");
  expect(await app.evaluate(() => window.__picks)).toEqual(["input_folder"]);
  await app.evaluate(() => (window.__nextPick = "synthetic-other"));
  await app.getByTestId("settings-input-dir-pick").click();
  await expect(folderInput(app, "input")).toHaveValue("synthetic-other");
  await app.getByTestId("settings-revert").click();
  await expect(folderInput(app, "input")).toHaveValue("typed-in");

  // Without pywebview (fresh page, no scripted bridge): an explanation, not a dead button.
  const plain = await app.context().newPage();
  await plain.goto("/next/");
  await plain.getByTestId("nav-settings").click();
  await plain.getByTestId("settings-output-dir-pick").click();
  await expect(plain.getByTestId("settings-output-dir-pick-error")).toContainText("Windowsデスクトップ版", { timeout: 10_000 });
  await expect(plain.getByTestId("settings-output-dir-pick")).toBeEnabled();
  await expect(plain.getByTestId("settings-output-dir-input").locator("input")).toHaveValue("");
  await shot(plain, "s1-02-host-unavailable");
  await plain.close();
});

test("token: register, confirm-delete, and the value never appears in DOM, console or errors", async ({ app, request }) => {
  const consoleText: string[] = [];
  app.on("console", (m) => consoleText.push(m.text()));
  const calls = recordApi(app);
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("token-status")).toHaveText("未設定");
  await expect(app.getByTestId("token-delete")).toBeDisabled();
  const input = app.getByTestId("token-input").locator("input");
  await expect(input).toHaveAttribute("type", "password");
  await expect(input).toHaveAttribute("autocomplete", "new-password");
  await input.fill(SECRET);
  await app.getByTestId("token-register").click();
  await expect(app.getByTestId("token-status")).toHaveText("登録済み（値は表示しません）");
  await expect(app.getByTestId("token-done")).toHaveText("登録しました。");
  await expect(input).toHaveValue("");
  expect(await app.content()).not.toContain(SECRET);
  expect(await app.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + location.href)).not.toContain(SECRET);
  const status = await (await request.get("/api/token", { headers: SESSION_HEADER })).json();
  expect(status).toMatchObject({ configured: true, available: true });
  expect(JSON.stringify(await (await request.get("/api/settings", { headers: SESSION_HEADER })).json())).not.toContain(SECRET);
  await shot(app, "s1-03-token-registered");

  // Delete needs confirmation; Esc cancels without a request.
  await app.getByTestId("token-delete").click();
  await expect(app.getByTestId("token-confirm-card")).toBeVisible();
  await app.keyboard.press("Escape");
  await expect(app.getByTestId("token-confirm-card")).toBeHidden();
  expect(calls.filter((r) => r.method() === "DELETE")).toHaveLength(0);
  await app.getByTestId("token-delete").click();
  await app.getByTestId("token-confirm-delete").click();
  await expect(app.getByTestId("token-status")).toHaveText("未設定");
  await expect(app.getByTestId("token-done")).toHaveText("削除しました。");
  expect(calls.filter((r) => r.method() === "DELETE")).toHaveLength(1);
  expect(consoleText.join("\n")).not.toContain(SECRET);
});

test("token: keyring 503 never claims 'unchanged'; written-then-read-back-failed is shown as unconfirmed", async ({ app, request }) => {
  await app.getByTestId("nav-settings").click();
  const input = app.getByTestId("token-input").locator("input");

  // Write rejected before storing → 503. The UI must not claim either outcome.
  await input.fill(REJECTED);
  await app.getByTestId("token-register").click();
  await expect(app.getByTestId("token-unconfirmed")).toHaveAttribute("data-reason", "store_error");
  await expect(app.getByTestId("token-card")).not.toContainText("変更されていません");
  await expect(app.getByTestId("token-card")).not.toContainText("keyring");
  await expect(input).toHaveValue(""); // cleared once sent; a retry is a fresh entry
  await expect(app.getByTestId("token-status")).toHaveText("現在の状態は未確認です。");
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-status")).toHaveText("未設定");

  // Written, then the read-back fails → real 503 although the token IS stored.
  await input.fill(READBACK);
  await app.getByTestId("token-register").click();
  await expect(app.getByTestId("token-unconfirmed")).toContainText("保存されたかどうかは確認できません");
  await expect(app.getByTestId("token-done")).toHaveText("");
  await expect(input).toHaveValue("");
  expect((await (await request.get("/api/token", { headers: SESSION_HEADER })).json()).configured).toBe(true);
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-status")).toHaveText("登録済み（値は表示しません）");
  await expect(app.getByTestId("token-unconfirmed-body")).toContainText("置き換わったかどうかは確認できない");
  await expect(app.getByTestId("token-action-error")).toHaveCount(0);
  await shot(app, "s1-04-token-503");
  await app.getByTestId("token-acknowledge").click();
  await expect(app.getByTestId("token-unconfirmed")).toHaveCount(0);
});

test("token: lost answers — current presence vs. unconfirmed PUT/DELETE, never a stale error or a kept secret", async ({ app }) => {
  const consoleText: string[] = [];
  app.on("console", (m) => consoleText.push(m.text()));
  await app.getByTestId("nav-settings").click();
  const input = app.getByTestId("token-input").locator("input");
  let mode: "applied" | "not-applied" = "applied";
  await app.route("**/api/token", async (route) => {
    const method = route.request().method();
    if (method === "GET") return route.continue();
    if (mode === "applied") await route.fetch(); // reaches the server, the answer is lost
    return route.abort("connectionreset");
  });

  // Missing → PUT applied, answer lost → re-check: "registered now", the PUT stays unconfirmed.
  await input.fill(SECRET);
  await app.getByTestId("token-register").click();
  await expect(app.getByTestId("token-unconfirmed")).toHaveAttribute("data-reason", "no_response");
  await expect(input).toHaveValue("");
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-status")).toHaveText("登録済み（値は表示しません）");
  await expect(app.getByTestId("token-unconfirmed-body")).toContainText("置き換わったかどうかは確認できない");
  await expect(app.getByTestId("token-action-error")).toHaveCount(0);
  await expect(app.getByTestId("token-done")).toHaveText("");

  // Registered → PUT of another value NOT applied, answer lost → still "registered", not "replaced".
  mode = "not-applied";
  await input.fill(OTHER);
  await app.getByTestId("token-register").click();
  await expect(app.getByTestId("token-status")).toHaveText("現在の状態は未確認です。");
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-status")).toHaveText("登録済み（値は表示しません）");
  await expect(app.getByTestId("token-unconfirmed-body")).toContainText("置き換わったかどうかは確認できない");
  await expect(app.getByTestId("token-done")).not.toContainText("登録しました");

  // DELETE applied, answer lost → "missing now", the delete itself unconfirmed.
  mode = "applied";
  await app.getByTestId("token-delete").click();
  await app.getByTestId("token-confirm-delete").click();
  await expect(app.getByTestId("token-unconfirmed")).toContainText("削除");
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-status")).toHaveText("未設定");
  await expect(app.getByTestId("token-unconfirmed-body")).toContainText("直前の削除によるものかどうかは確認できません");

  // Re-check itself failing keeps "unknown".
  await input.fill(OTHER);
  await app.getByTestId("token-register").click();
  await app.unroute("**/api/token");
  await app.route("**/api/token", (route) => route.abort("connectionreset"));
  await app.getByTestId("token-recheck").click();
  await expect(app.getByTestId("token-recheck-failed")).toBeVisible();
  await expect(app.getByTestId("token-status")).toHaveText("現在の状態は未確認です。");

  for (const value of [SECRET, OTHER]) {
    expect(await app.content()).not.toContain(value);
    expect(consoleText.join("\n")).not.toContain(value);
    expect(await app.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + location.href)).not.toContain(value);
  }
});

test("token: store unavailable is not 'not configured'; portable says session only", async ({ app }) => {
  await app.route("**/api/token", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({ json: { configured: false, available: false, backend: "unavailable", error_type: "X", error_message: "synthetic detail" } });
  });
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("token-status")).toHaveText("OSの資格情報保存機能を利用できません。");
  await expect(app.getByTestId("token-card")).not.toContainText("synthetic detail");
  await app.unroute("**/api/token");

  const page = await app.context().newPage();
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...(await response.json()), portable_distribution: true, token_session_only: true } });
  });
  await page.route("**/api/token", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: { configured: false, available: true, backend: "session" } }) : route.continue(),
  );
  await page.goto("/next/");
  await page.getByTestId("nav-settings").click();
  await expect(page.getByTestId("token-status")).toHaveText("未設定");
  await expect(page.getByTestId("token-session-only")).toContainText("ディスクには保存しません");
  await expect(page.getByTestId("token-register")).toHaveText("このセッションで使用");
  await expect(page.getByTestId("settings-portable")).toBeVisible();
  await expect(page.getByTestId("token-card")).not.toContainText("永続");
  await page.close();
});

test("settings: an unanswered PATCH is re-checked by the fields it sent, separately from later edits", async ({ app, request }) => {
  const calls = recordApi(app);
  await app.getByTestId("nav-settings").click();
  let mode: "lost-before" | "lost-after" = "lost-before";
  await app.route("**/api/settings", async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    if (mode === "lost-after") await route.fetch(); // applied on the server, answer lost
    return route.abort("connectionreset");
  });
  await choose(app, "settings-profile", "Vulkan");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-save-unknown")).toBeVisible();
  await expect(app.getByTestId("settings-save-unknown-fields")).toHaveText("送信した項目: 既定プロファイル");
  // No second save over an unconfirmed one.
  await choose(app, "settings-theme", "ダーク");
  await expect(app.getByTestId("settings-save")).toBeDisabled();
  await app.getByTestId("settings-reconcile").click();
  await expect(app.getByTestId("settings-reconciled")).toHaveAttribute("data-result", "mismatch");
  await expect(app.getByTestId("settings-reconciled-mismatched")).toContainText("既定プロファイル");
  await expect(app.getByTestId("settings-profile")).toContainText("Vulkan");
  await expect(app.getByTestId("settings-theme")).toContainText("ダーク");

  // Applied but lost; then a further edit. The sent fields match; the later edit stays unsaved.
  mode = "lost-after";
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-save-unknown-fields")).toHaveText("送信した項目: 外観、既定プロファイル");
  await choose(app, "settings-language", "English");
  await shot(app, "s1-05-save-unknown");
  await app.getByTestId("settings-reconcile").click();
  await expect(app.getByTestId("settings-reconciled")).toHaveAttribute("data-result", "match");
  await expect(app.getByTestId("settings-reconciled-matched")).toContainText("外観、既定プロファイル");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  await expect(app.getByTestId("settings-language")).toContainText("English");
  await expect(app.locator(".v-theme--dark").first()).toBeAttached(); // server-confirmed theme applied
  expect(calls.filter((r) => r.method() === "PATCH")).toHaveLength(2); // one per click, none automatic
  const stored = await (await request.get("/api/settings", { headers: SESSION_HEADER })).json();
  expect(stored).toMatchObject({ default_profile: "vulkan", theme: "dark", language: "ja" });
});

test("logs: server log_dir shown; raw/fallback warnings; open uses exactly that path; failures shown", async ({ app }) => {
  await app.route("**/api/settings", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...(await response.json()), log_dir: "synthetic-fallback-logs", log_dir_fallback: true, raw_subprocess_logs: true } });
  });
  const opened: unknown[] = [];
  let answer = 204;
  await app.route("**/api/open-folder", async (route) => {
    opened.push(route.request().postDataJSON());
    return route.fulfill({ status: answer, json: answer === 204 ? undefined : { detail: "synthetic" } });
  });
  await app.reload();
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("log-dir")).toHaveText("synthetic-fallback-logs");
  await expect(app.getByTestId("log-fallback")).toBeVisible();
  await expect(app.getByTestId("log-raw-warning")).toContainText("機密情報が含まれる可能性");
  await shot(app, "s1-06-logs-raw");
  await app.getByTestId("log-open").click();
  await expect(app.getByTestId("log-open-status")).toHaveText("フォルダを開く操作を実行しました。");
  answer = 503;
  await app.getByTestId("log-open").click();
  await expect(app.getByTestId("log-open-error")).toHaveText("フォルダを開けませんでした（ファイルマネージャーを起動できません）。");
  answer = 400;
  await app.getByTestId("log-open").click();
  await expect(app.getByTestId("log-open-error")).toHaveText("フォルダが見つかりません。");
  expect(opened).toEqual([{ path: "synthetic-fallback-logs" }, { path: "synthetic-fallback-logs" }, { path: "synthetic-fallback-logs" }]);
});

test("logs: the real server log folder is shown and the button is disabled when none is known", async ({ app, request }) => {
  const real = (await (await request.get("/api/settings", { headers: SESSION_HEADER })).json()).log_dir as string;
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("log-dir")).toHaveText(real);
  await expect(app.getByTestId("log-raw-warning")).toHaveCount(0);
  await app.route("**/api/settings", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...(await response.json()), log_dir: "" } });
  });
  await app.reload();
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("log-dir")).toHaveText("取得できませんでした");
  await expect(app.getByTestId("log-open")).toBeDisabled();
});

test("setup: read-only status and a confirmed hand-over to the legacy screen; nothing is started", async ({ app, request }) => {
  // --next-ui never activates v1.
  expect((await request.get("/api/v1/capabilities", { headers: SESSION_HEADER })).status()).toBe(404);
  const calls = recordApi(app);
  const unsafeFromNext: string[] = [];
  app.on("request", (r) => {
    if (app.url().includes("/next/") && r.method() !== "GET") unsafeFromNext.push(`${r.method()} ${new URL(r.url()).pathname}`);
  });
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("setup-status")).toContainText("状態:");
  await choose(app, "settings-theme", "ダーク");
  await app.getByTestId("setup-open-legacy").click();
  await expect(app.getByTestId("leave-dialog-card")).toBeVisible();
  await shot(app, "s1-07-leave-dialog");
  await app.getByTestId("leave-cancel").click();
  await expect(app.getByTestId("leave-dialog-card")).toBeHidden();
  await expect(app).toHaveURL(/\/next\/$/);
  await app.getByTestId("setup-open-legacy").click();
  await app.getByTestId("leave-save").click();
  await expect(app).toHaveURL(/127\.0\.0\.1:\d+\/$/);
  await expect(app.locator("#view-settings")).toBeAttached();
  // From /next/ only the explicit save; no wizard start, setup job or v1 call.
  expect(unsafeFromNext).toEqual(["PATCH /api/settings"]);
  expect(calls.some((r) => new URL(r.url()).pathname.startsWith("/api/v1"))).toBe(false);
});

test("leaving with unsaved settings from the app bar: discard goes without saving", async ({ app, request }) => {
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-language", "English");
  await app.getByTestId("open-legacy").click();
  await app.getByTestId("leave-discard").click();
  await expect(app).toHaveURL(/127\.0\.0\.1:\d+\/$/);
  expect((await (await request.get("/api/settings", { headers: SESSION_HEADER })).json()).language).toBe("ja");
});

test("draft, picker answer and token state survive navigation, mode switch and a narrow window", async ({ app }) => {
  await withNativeBridge(app);
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-profile", "CPU");
  await app.evaluate(() => (window.__nextPick = "synthetic-narrow"));
  await app.getByTestId("settings-input-dir-pick").click();
  await app.getByTestId("mode-expert").click();
  await app.getByTestId("nav-transcribe").click();
  await expect(app.getByTestId("expert-workspace")).toBeVisible();
  await app.getByTestId("mode-normal").click();
  await app.setViewportSize({ width: 900, height: 660 });
  await app.getByTestId("nav-settings").click();
  await expect(app.getByTestId("settings-profile")).toContainText("CPU");
  await expect(folderInput(app, "input")).toHaveValue("synthetic-narrow");
  await expect(app.getByTestId("settings-status")).toHaveText("未保存の変更があります。");
  const overflow = await app.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(app, "s1-08-narrow");
});

test("English: the S1 page is fully translated; keyboard reaches the folder chooser", async ({ app }) => {
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-language", "English");
  await app.getByTestId("settings-save").click();
  await expect(app.getByTestId("settings-status")).toHaveText("Saved.");
  for (const text of ["Default profile", "Default input folder", "Hugging Face token", "Logs", "Open setup (legacy screen)", "Not configured"]) {
    await expect(app.getByTestId("settings-page")).toContainText(text);
  }
  await expect(app.getByTestId("settings-page")).not.toContainText("既定");
  await folderInput(app, "input").focus();
  await app.keyboard.press("Tab");
  await expect(app.getByTestId("settings-input-dir-pick")).toBeFocused();
  await shot(app, "s1-09-english");
});
