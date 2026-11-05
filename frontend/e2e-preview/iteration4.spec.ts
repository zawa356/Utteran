import { appBarOverlaps, choose, columns, expect, horizontalOverflow, lowContrastText, paneTops, readability, settled, setup, shot, test } from "./fixtures";

/**
 * UI-3-0 iteration 4 (owner memos #15–#24). Synthetic data only; every test also fails on any
 * request outside the dev server, any /api or /next request and any console error (fixtures.ts).
 * Exports end at a "save as" step that writes nothing: no download may start.
 */

test.beforeEach(async ({ preview }) => {
  preview.on("download", (d) => {
    throw new Error(`unexpected download ${d.suggestedFilename()}`);
  });
});

// ---- #19 Expert processing settings --------------------------------------------------------------
test("#19 Expert: categories keep what was typed — prompt (Japanese), glossary, beam, engine-specific fields", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("category-tabs").getByRole("button")).toHaveCount(5);

  await preview.getByTestId("category-prompt").click();
  const prompt = preview.getByTestId("input-initialPrompt").locator("textarea");
  await prompt.fill("社内の定例会議です。話題は製品の開発計画です。");
  await expect(preview.getByTestId("input-initialPrompt")).toContainText("23 / 32768文字");
  const term = preview.getByTestId("glossary-new").locator("input");
  await term.fill("議事録");
  await term.press("Enter");
  await term.fill("Utteran");
  await preview.getByTestId("glossary-add").click();
  await term.fill("utteran");
  await expect(preview.getByTestId("glossary-new")).toContainText("同じ語句がすでにあります");
  await expect(preview.getByTestId("glossary-add")).toBeDisabled();
  await expect(preview.getByTestId("glossary-count")).toHaveText("2語（最大1000）");

  await preview.getByTestId("category-asr").click();
  await preview.getByTestId("input-beamSize-common").locator("input").fill("150");
  await expect(preview.getByTestId("input-beamSize-common")).toContainText("1〜100の範囲");
  await expect(preview.getByTestId("category-asr-issues")).toBeVisible();
  await preview.getByTestId("input-row-in-01").click();
  await expect(preview.getByTestId("submit-blocked")).toContainText("設定に問題があります");
  await preview.getByTestId("input-beamSize-common").locator("input").fill("8");
  await expect(preview.getByTestId("category-asr-issues")).toHaveCount(0);

  // Only the chosen backend's own fields are shown.
  await preview.getByTestId("category-advanced").click();
  await expect(preview.getByTestId("engine-note")).toContainText("自動");
  await expect(preview.getByTestId("input-wcThreads-common")).toHaveCount(0);
  await preview.getByTestId("category-asr").click();
  await expect(preview.getByTestId("input-computeType-common")).toHaveCount(0);
  await choose(preview, "input-backend-common", "faster-whisper");
  await expect(preview.getByTestId("input-computeType-common")).toBeVisible();
  await expect(preview.getByTestId("input-model-common")).toContainText("large-v3-turbo"); // model follows the engine
  await choose(preview, "input-backend-common", "whisper.cpp");
  await expect(preview.getByTestId("input-computeType-common")).toHaveCount(0);
  await preview.getByTestId("category-advanced").click();
  await preview.getByTestId("input-wcThreads-common").locator("input").fill("6");
  await preview.getByTestId("section-whisperCppThresholds").click();
  await expect(preview.getByTestId("input-wcNoSpeechThreshold-common")).toBeVisible();

  // Back and forth between categories and panes: nothing is lost.
  await preview.getByTestId("category-prompt").click();
  await expect(prompt).toHaveValue("社内の定例会議です。話題は製品の開発計画です。");
  await expect(preview.getByTestId("glossary-edit-0").locator("input")).toHaveValue("議事録");
  await preview.getByTestId("category-asr").click();
  await expect(preview.getByTestId("input-beamSize-common").locator("input")).toHaveValue("8");
  await preview.getByTestId("mode-normal").click();
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("category-asr")).toHaveAttribute("aria-pressed", "true");
  await preview.getByTestId("category-advanced").click();
  await expect(preview.getByTestId("input-wcThreads-common").locator("input")).toHaveValue("6");
});

test("#19 IME: text is applied on compositionend, Enter during composition never adds a glossary term", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("category-prompt").click();
  const term = preview.getByTestId("glossary-new").locator("input");
  await term.focus();
  await term.dispatchEvent("compositionstart");
  await preview.keyboard.insertText("ぎじろく");
  await term.dispatchEvent("keydown", { key: "Enter", isComposing: true, keyCode: 229 });
  await expect(preview.getByTestId("glossary-count")).toHaveText("0語（最大1000）");
  await term.dispatchEvent("compositionend");
  await term.press("Enter");
  await expect(preview.getByTestId("glossary-count")).toHaveText("1語（最大1000）");
  await expect(preview.getByTestId("glossary-edit-0").locator("input")).toHaveValue("ぎじろく");
});

test("#19 common vs individual: per-file override, mixed values kept, snapshot frozen; no prompt in the job view", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-01").click();
  await preview.getByTestId("input-row-in-02").click({ modifiers: ["Control"] });
  await preview.getByTestId("inspector-tab-selection").click();
  await expect(preview.getByTestId("field-language-source")).toHaveText("一部が個別");
  await expect(preview.getByTestId("field-language-mixed")).toBeVisible();
  await preview.getByTestId("field-language-override").locator("input").click(); // individual: each keeps its value
  await expect(preview.getByTestId("field-language-source")).toHaveText("個別");
  await expect(preview.getByTestId("field-language-mixed")).toBeVisible();
  await preview.getByTestId("category-prompt").click();
  await preview.getByTestId("field-initialPrompt-override").locator("input").click();
  await preview.getByTestId("input-initialPrompt").locator("textarea").fill("取材の録音です。");
  await expect(preview.getByTestId("category-prompt-overrides")).toHaveText("1");
  await expect(preview.getByTestId("input-row-in-01-override")).toHaveText("2");

  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("submit-result")).toContainText("2件をキューに追加しました。");
  await preview.getByTestId("input-initialPrompt").locator("textarea").fill("変更後の文章");
  await preview.getByTestId("nav-queue").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  await expect(preview.getByTestId("job-snapshot")).toContainText("プロンプトあり（8文字）");
  await expect(preview.getByTestId("job-detail")).not.toContainText("取材の録音");
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-jobLog").click();
  await expect(preview.getByTestId("log-lines")).not.toContainText("取材の録音");
});

test("#19 Normal edits apply to the shown file only; Expert files and common defaults are untouched", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await choose(preview, "input-language-normal", "英語");
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("input-row-in-01-override")).toHaveText("1"); // the shown file
  await expect(preview.getByTestId("input-row-in-03-override")).toHaveCount(0); // others unchanged
  await preview.getByTestId("inspector-tab-common").click();
  await expect(preview.getByTestId("input-language-common")).toContainText("日本語");
  await expect(preview.getByTestId("category-tabs")).toBeVisible();
  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("normal-settings")).not.toContainText("initial_prompt"); // Expert's long form stays out of Normal
});

test("#20 presets: apply with a preview of what changes; manage, import (samples) and export", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await choose(preview, "preset-apply-select", "字幕づくり");
  await expect(preview.getByTestId("preset-apply-preview")).toContainText("共通の既定値の3項目");
  await preview.getByTestId("preset-apply-button").click();
  await preview.getByTestId("category-diarization").click();
  await expect(preview.getByTestId("input-diarization-common").locator("input")).not.toBeChecked();
  await preview.getByTestId("input-row-in-04").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await choose(preview, "preset-apply-select", "定例会議（用語集つき）");
  await expect(preview.getByTestId("preset-apply-preview")).toContainText("選択した1件に");

  await preview.getByTestId("nav-settings").click();
  await preview.getByTestId("settings-cat-presets").click();
  await expect(preview.getByTestId("preset-delete")).toBeDisabled(); // built-in
  await preview.getByTestId("preset-duplicate").click();
  await expect(preview.getByTestId("settings-presets").locator(".v-list-item")).toHaveCount(5);
  await expect(preview.getByTestId("preset-detail")).toContainText("標準のコピー");
  await preview.getByTestId("preset-edit").click();
  await preview.getByTestId("preset-name").locator("input").fill("社内用");
  await preview.getByTestId("preset-name-save").click();
  await expect(preview.getByTestId("preset-detail")).toContainText("社内用");
  await preview.getByTestId("preset-delete").click();
  await preview.getByTestId("preset-delete-confirm").click();
  await expect(preview.getByTestId("settings-presets").locator(".v-list-item")).toHaveCount(4);

  await preview.getByTestId("preset-import").click();
  const imp = preview.getByTestId("preset-import-dialog");
  await imp.getByTestId("import-sample-broken").click();
  await expect(imp.getByTestId("import-issue-notJson")).toBeVisible();
  await expect(imp.getByTestId("import-load")).toHaveCount(0);
  await imp.getByTestId("import-other").click();
  await imp.getByTestId("import-sample-oldVersion").click();
  await expect(imp.getByTestId("import-issue-version")).toContainText("バージョン0");
  await imp.getByTestId("import-other").click();
  await imp.getByTestId("import-sample-unsupported").click();
  await expect(imp.getByTestId("import-issue-secretField")).toContainText("diarization.hf_token");
  await expect(imp.getByTestId("import-issue-badValue")).toContainText("custom-engine");
  await imp.getByTestId("import-other").click();
  await imp.getByTestId("import-sample-duplicate").click();
  await expect(imp.getByTestId("import-duplicate")).toBeVisible();
  await expect(imp.getByTestId("import-name").locator("input")).toHaveValue("定例会議（用語集つき） (2)");
  await imp.getByTestId("import-load").click();
  await expect(preview.getByTestId("settings-presets").locator(".v-list-item")).toHaveCount(5);

  await preview.getByTestId("preset-row-user-01").click();
  await preview.getByTestId("preset-export").click();
  const exp = preview.getByTestId("preset-export-dialog");
  await expect(exp.getByTestId("export-json")).toContainText('"format_version": 1');
  await expect(exp.getByTestId("export-json")).not.toContainText("議事録");
  await expect(exp.getByTestId("export-omitted")).toContainText("初期プロンプト");
  await exp.getByTestId("export-include-text").locator("input").click();
  await expect(exp.getByTestId("export-text-warning")).toBeVisible();
  await expect(exp.getByTestId("export-json")).toContainText("議事録");
  await exp.getByTestId("preset-save-button").click();
  await expect(exp.getByTestId("preset-save-unavailable")).toContainText("保存できません");
});

// ---- #24 / #23 setup wizard --------------------------------------------------------------------
test("#24 first run: welcome → detect → CPU → no diarization → review → approve → stages → done → Normal home", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-step", "welcome");
  await expect(preview.getByTestId("mode-toggle")).toHaveCount(0); // no mode choice before setup
  await expect(preview.getByTestId("top-nav")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-back")).toBeDisabled();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-position")).toHaveText("手順 2/4: 環境の確認");
  await preview.getByTestId("wizard-device-cpu").locator("input").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-back").click(); // back keeps the choice
  await expect(preview.getByTestId("wizard-device-cpu").locator("input")).toBeChecked();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-diarization").locator("input")).not.toBeChecked();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-position")).toHaveText("手順 4/4: インストール内容の確認"); // no credential step
  await expect(preview.getByTestId("plan-item-runtime")).toContainText("CPU用の実行環境");
  await expect(preview.getByTestId("plan-item-diarizationModel")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-no-side-effects")).toBeVisible();
  await preview.getByTestId("wizard-approve").click();
  await expect(preview.getByTestId("wizard-run")).toHaveAttribute("data-run", "running");
  await expect(preview.getByTestId("wizard-close")).toBeDisabled();
  for (let i = 0; i < 12 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await expect(preview.getByTestId("wizard-done")).toContainText("実際には何もダウンロード・インストールしていません");
  await preview.getByTestId("wizard-start-transcribing").click();
  await expect(preview.getByTestId("normal-home")).toBeVisible();
  await expect(preview.getByTestId("mode-toggle")).toBeVisible();
  await expect(preview.getByTestId("normal-start")).toBeEnabled(); // "no diarization" became the default
  await expect(preview.getByTestId("input-diarization-normal").locator("input")).not.toBeChecked();
  await expect(preview.getByTestId("normal-slow-note")).toContainText("CPU");
});

test("#24 GPU + diarization: the credential step appears only when needed; close keeps the choices", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-diarization").locator("input").click();
  await expect(preview.getByTestId("wizard-needs-credential")).toBeVisible();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-credentials")).toBeVisible();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled();
  await expect(preview.getByTestId("wizard-next-block")).toContainText("トークンを登録");
  await expect(preview.getByTestId("wizard-credentials").locator("input[type=password], input[type=text]")).toHaveCount(0); // no token is typed
  await preview.getByTestId("wizard-register-credential").click();
  await expect(preview.getByTestId("wizard-credential-state")).toContainText("値は表示しません");
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await preview.getByTestId("wizard-close").click();
  await preview.getByTestId("wizard-close-confirm").click();
  await expect(preview.getByTestId("setup-notice")).toContainText("完了していません");
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await preview.getByTestId("setup-notice-continue").click();
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-step", "welcome");
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-agreement").locator("input")).toBeChecked(); // kept
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("plan-item-diarizationModel")).toBeVisible();
});

test("#24 CPU only: the GPU cannot be chosen, the wizard still completes", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回・GPUなし/);
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-device-gpu").locator("input")).toBeDisabled();
  await expect(preview.getByTestId("wizard-environment")).toContainText("GPUが見つからないため選べません");
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-approve").click();
  for (let i = 0; i < 12 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await expect(preview.getByTestId("wizard-done")).toBeVisible();
});

test("#24 interrupted: resume from the stopped stage → done", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^途中で中断/);
  await expect(preview.getByTestId("wizard-run")).toHaveAttribute("data-run", "interrupted");
  await expect(preview.getByTestId("wizard-interrupted")).toContainText("文字起こしモデルの取得");
  await expect(preview.getByTestId("wizard-stage-runtime")).toHaveAttribute("data-status", "done");
  await preview.getByTestId("wizard-continue").click();
  await expect(preview.getByTestId("wizard-stage-asr_model")).toHaveAttribute("data-status", "running");
  await expect(preview.getByTestId("wizard-stage-runtime")).toHaveAttribute("data-status", "done");
  for (let i = 0; i < 8 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await preview.getByTestId("wizard-start-transcribing").click();
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
});

test("#24 failure: short reason → log → change settings (new approval) → retry; never a dead end", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^セットアップ失敗/);
  const failed = preview.getByTestId("wizard-failed");
  await expect(failed).toHaveAttribute("role", "alert");
  await expect(failed).toContainText("「文字起こしモデルの取得」に失敗しました");
  await expect(failed).toContainText("demo_download_failed");
  await preview.getByTestId("wizard-log").click();
  await expect(preview.getByTestId("dialog-setupLog").getByTestId("log-lines")).toContainText("失敗: 文字起こしモデルの取得");
  await preview.getByTestId("dialog-setupLog").getByTestId("log-export").click();
  await expect(preview.getByTestId("log-save-name")).toHaveText("utteran-setup-20261105.log");
  await preview.getByTestId("dialog-close").click();
  await preview.getByTestId("wizard-change").click();
  await expect(preview.getByTestId("wizard-features")).toBeVisible();
  await preview.getByTestId("wizard-diarization").locator("input").click(); // off
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-review")).toBeVisible();
  await preview.getByTestId("wizard-approve").click();
  await expect(preview.getByTestId("wizard-stage-runtime")).toHaveAttribute("data-status", "done"); // kept
  await preview.getByTestId("wizard-demo-fail").click();
  await preview.getByTestId("wizard-continue").click(); // retry
  await expect(preview.getByTestId("wizard-run")).toHaveAttribute("data-run", "running");
});

test("#23 Setup page: re-detect and set up are separate; repair keeps the working profile usable", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^構成に問題/);
  await expect(preview.getByTestId("normal-repair-note")).toContainText("CPUで実行します");
  await expect(preview.getByTestId("normal-start")).toBeEnabled(); // the CPU profile still works
  await preview.getByTestId("topnav-setup").click();
  await preview.getByTestId("env-redetect").click();
  await expect(preview.getByTestId("env-checking")).toBeVisible(); // re-detect: a refresh, no wizard
  await expect(preview.getByTestId("setup-wizard")).toHaveCount(0);
  await preview.getByTestId("env-finish").click();
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-repair")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").locator("[data-testid^=env-action-]")).toHaveCount(0); // never reinstall what works
  await preview.getByTestId("env-action-repair-gpu").click();
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-entry", "repair");
  await expect(preview.getByTestId("wizard-review")).toContainText("修復する内容");
  await expect(preview.getByTestId("sidebar")).toBeVisible(); // add/repair keep the app navigation
  await preview.getByTestId("wizard-approve").click();
  for (let i = 0; i < 6 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await preview.getByTestId("wizard-back-to-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();

  await preview.getByTestId("env-setup").click(); // "Set up…" = add / change
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-entry", "add");
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-step", "environment");
  await preview.getByTestId("wizard-close").click();
  await expect(preview.getByTestId("section-setup")).toBeVisible();
});

test("#23 missing diarization token: a blocked start explains it; the card offers 'Set up diarization…'", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^話者分離のトークンなし/);
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("話者分離はまだ設定されていません");
  await preview.getByTestId("normal-blocked-setup").click();
  await preview.getByTestId("env-action-diarization-gpu").click();
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-step", "environment");
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-diarization").locator("input")).toBeChecked();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-credentials")).toBeVisible();
  await preview.getByTestId("wizard-skip-diarization").click();
  await expect(preview.getByTestId("wizard-review")).toBeVisible();
});

// ---- #17 #15 logs and diagnostics ------------------------------------------------------------------
test("#17 log export: system log and ONE job's log, file name and format, 'cannot save' at the exit", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-systemLog").click();
  await preview.getByTestId("log-export").click();
  await expect(preview.getByTestId("log-save-target")).toHaveText("システムログ（表示中の全件）");
  await expect(preview.getByTestId("log-save-name")).toHaveText("utteran-system-20261105.log");
  await choose(preview, "log-export-format", /\.txt/);
  await expect(preview.getByTestId("log-save-name")).toHaveText("utteran-system-20261105.txt");
  await preview.getByTestId("log-save-button").click();
  await expect(preview.getByTestId("log-save-unavailable")).toContainText("この試作では保存できません");
  await preview.getByTestId("dialog-close").click();

  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-jobLog").click();
  await choose(preview, "joblog-select", /demo-job-003/);
  await preview.getByTestId("log-export").click();
  await expect(preview.getByTestId("log-save-target")).toHaveText("ジョブ demo-job-003 のログ");
  await expect(preview.getByTestId("log-save-name")).toHaveText("utteran-demo-job-003-20261105.log");
  await preview.getByTestId("log-save-back").click();
  await expect(preview.getByTestId("log-lines")).toBeVisible();
});

test("#15 diagnostics: one purpose line, one privacy line, Save…; the lists only behind 'What is included'", async ({ preview }) => {
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-diagnostics").click();
  const diag = preview.getByTestId("dialog-diagnostics");
  await expect(diag).toContainText("診断情報のエクスポート");
  await expect(diag.getByTestId("diag-privacy")).toContainText("トークン・プロンプトは含みません");
  await expect(diag.getByTestId("diag-included")).toBeHidden();
  await expect(diag.getByTestId("diag-excluded")).toBeHidden();
  await diag.getByTestId("diag-details").click();
  await expect(diag.getByTestId("diag-excluded")).toContainText("プロンプト・用語ヒントの内容");
  await diag.getByTestId("diag-export").click();
  await expect(diag.getByTestId("diag-save-name")).toHaveText("utteran-diagnostics-20261105.zip");
  await diag.getByTestId("diag-save-button").click();
  await expect(diag.getByTestId("diag-save-unavailable")).toBeVisible();
});

// ---- #16 #18 wording and icon ----------------------------------------------------------------------
test("#18 the existing Utteran icon left of the name and in About; #16 plain wording", async ({ preview }) => {
  const icon = preview.getByTestId("brand").getByTestId("app-icon");
  await expect(icon).toBeVisible();
  const box = await icon.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(20);
  expect(box!.width).toBeLessThanOrEqual(24);
  expect(await icon.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  expect(await icon.getAttribute("src")).toMatch(/utteran\.svg/);
  const brandBox = await preview.getByTestId("brand").boundingBox();
  expect(box!.x).toBeLessThan(brandBox!.x + 20); // left of the name
  await preview.getByTestId("menu-help").click();
  await preview.getByTestId("menu-item-about").click();
  await expect(preview.getByTestId("dialog-about").getByTestId("app-icon")).toBeVisible();
  await preview.getByTestId("dialog-ok").click();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-setup").click();
  await preview.getByTestId("env-toggle-gpu").click();
  expect(await preview.locator("body").innerText()).not.toMatch(/判定の根拠|必要な操作/);
});

// ---- #22 themes -------------------------------------------------------------------------------------
test("#22 colour theme (green / blue) and appearance (system / light / dark) are independent settings", async ({ preview }) => {
  const primary = () => preview.evaluate(() => getComputedStyle(document.querySelector(".v-application")!).getPropertyValue("--v-theme-primary").trim());
  const success = () => preview.evaluate(() => getComputedStyle(document.querySelector(".v-application")!).getPropertyValue("--v-theme-success").trim());
  await preview.getByTestId("topnav-settings").click();
  await preview.getByTestId("settings-accent-blue").click();
  expect(await primary()).toBe("47,93,138");
  const okLight = await success();
  await choose(preview, "settings-theme", "ダーク");
  expect(await primary()).toBe("141,180,220");
  await expect(preview.getByTestId("preview-ready")).toHaveAttribute("data-theme-name", "blueDark");
  await preview.getByTestId("settings-accent-green").click();
  await expect(preview.getByTestId("preview-ready")).toHaveAttribute("data-theme-name", "greenDark"); // appearance kept
  await choose(preview, "settings-theme", "ライト");
  expect(await success()).toBe(okLight); // the semantic colour does not follow the accent
});

const COMBOS = [
  { accent: "green", theme: "light" },
  { accent: "green", theme: "dark" },
  { accent: "blue", theme: "light" },
  { accent: "blue", theme: "dark" },
] as const;
for (const c of COMBOS) {
  test(`#22 contrast ${c.accent}/${c.theme}: Normal, Expert editor, Wizard, dialog — text ≥ 4.5:1, no small text or buttons`, async ({ preview }) => {
    await preview.getByTestId(`palette-${c.accent}`).click();
    await setup(preview, { theme: c.theme, scenario: /^複数ファイル・処理中/ });
    await preview.getByTestId("toolbar-toggle").click();
    const check = async (where: string) => {
      await settled(preview);
      expect(await lowContrastText(preview), where).toEqual([]);
      const r = await readability(preview);
      expect(r.smallText, where).toEqual([]);
      expect(r.smallButtons, where).toEqual([]);
    };
    await check("normal");
    await preview.getByTestId("mode-expert").click();
    await preview.getByTestId("category-prompt").click();
    await check("expert prompt");
    await preview.getByTestId("category-advanced").click();
    await check("expert advanced");
    await preview.getByTestId("nav-setup").click();
    await preview.getByTestId("env-setup").click();
    await check("wizard");
    await preview.getByTestId("menu-tools").click();
    await preview.getByTestId("menu-item-diagnostics").click();
    await check("dialog");
  });
}

// ---- #21 narrow windows -----------------------------------------------------------------------------
const SIZES = [
  { name: "900x660", width: 900, height: 660 },
  { name: "1280x820", width: 1280, height: 820 },
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "zoom125", width: 1024, height: 656 },
  { name: "zoom150", width: 853, height: 547 },
  { name: "zoom200", width: 640, height: 410 },
];
for (const z of SIZES) {
  test(`#21 ${z.name}: Expert panes are one row (never stacked), submit reachable, no overflow`, async ({ preview }) => {
    await preview.setViewportSize({ width: z.width, height: z.height });
    await setup(preview, { scenario: /^複数ファイル・未投入/ });
    await preview.getByTestId("toolbar-toggle").click();
    await preview.getByTestId("mode-expert").click();
    const cols = await columns(preview);
    expect(new Set(await paneTops(preview)).size, `${cols} columns`).toBe(1);
    expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
    expect(await appBarOverlaps(preview)).toEqual([]);
    if (cols < 3) await preview.getByTestId("pane-switch-queue").click();
    await expect(preview.getByTestId("submit-selected")).toBeInViewport();
    if (cols < 3) await preview.getByTestId("pane-switch-inspector").click();
    await preview.getByTestId("category-advanced").click();
    await expect(preview.getByTestId("category-advanced")).toBeInViewport();
    expect(new Set(await paneTops(preview)).size).toBe(1);
    expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
  });
}

test("#21 1280 px: a typical file name is not cut in the files pane", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await settled(preview);
  const cut = await preview.getByTestId("input-row-in-02").locator(".p-explorer__name .p-ellipsis").evaluate((el) => el.scrollWidth > el.clientWidth);
  expect(cut).toBe(false); // "interview-sample-02.wav"
});

// ---- iteration 4 P1 fixes ------------------------------------------------------------------------------
test("P1-1 credential present, terms NOT accepted: the terms step shows and blocks; approve only after accepting", async ({ preview }) => {
  // "Set up" (default example): a diarization credential already exists.
  await preview.getByTestId("topnav-setup").click();
  await preview.getByTestId("env-setup").click();
  await preview.getByTestId("wizard-next").click(); // environment → features
  await preview.getByTestId("wizard-diarization").locator("input").click();
  await preview.getByTestId("wizard-next").click();
  const cred = preview.getByTestId("wizard-credentials");
  await expect(cred).toBeVisible();
  await expect(preview.getByTestId("wizard-credential-state")).toHaveAttribute("data-present", "true");
  await expect(preview.getByTestId("wizard-register-credential")).toHaveCount(0); // "Register…" only while missing
  await expect(preview.getByTestId("wizard-agreement").locator("input")).not.toBeChecked();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled();
  await expect(preview.getByTestId("wizard-next-block")).toContainText("利用条件に同意すると");
  await shot(preview, "p1-credential-present-terms-missing");
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("plan-agreement")).toHaveAttribute("data-ok", "true");
  await expect(preview.getByTestId("plan-credential")).toHaveAttribute("data-ok", "true");
  await expect(preview.getByTestId("wizard-approve")).toBeEnabled();
  // Withdrawing the acceptance on the way back keeps "next" blocked again.
  await preview.getByTestId("wizard-back").click();
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled();
  await preview.getByTestId("lang-en").click();
  await expect(preview.getByTestId("wizard-next-block")).toHaveText("Accept the terms to continue.");
  await expect(cred).toContainText("Token: registered (value not shown)");
});

test("P1-1 diarization off: no token or terms asked, approvable, and no false 'accepted' on the review", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-review")).toBeVisible();
  await expect(preview.getByTestId("plan-agreement")).toHaveCount(0);
  await expect(preview.getByTestId("plan-credential")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-review")).not.toContainText("同意済み");
  await expect(preview.getByTestId("wizard-approve")).toBeEnabled();
});

test("P1-1 token missing, terms accepted: still blocked on the token; 'continue without diarization' clears both", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^話者分離のトークンなし/);
  await preview.getByTestId("topnav-setup").click();
  await preview.getByTestId("env-action-diarization-gpu").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled();
  await expect(preview.getByTestId("wizard-next-block")).toContainText("トークンを登録すると");
  await preview.getByTestId("wizard-skip-diarization").click();
  await expect(preview.getByTestId("plan-agreement")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-approve")).toBeEnabled();
});

test("P1-2 GPU ready + diarization ready → add CPU: GPU and diarization stay, jobs and settings untouched; nothing changes on failure", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await choose(preview, "toolbar-env", /^GPUのみ/);
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("queue-now").locator("li")).toHaveCount(4);
  const jobsBefore = await preview.getByTestId("queue-now").innerText();
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveText("2");
  await preview.getByTestId("nav-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-unavailable")).toBeVisible();

  await preview.getByTestId("env-action-addCpu-cpu").click();
  await expect(preview.getByTestId("wizard-device-cpu").locator("input")).toBeChecked();
  await expect(preview.getByTestId("wizard-installed-gpu")).toBeVisible();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-diarization-kept")).toBeVisible();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-approve").click();
  await preview.getByTestId("wizard-demo-fail").click();
  await preview.getByTestId("wizard-close").click();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-unavailable")).toBeVisible(); // failure: unchanged
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();

  await preview.getByTestId("env-setup").click(); // the stopped operation is shown again
  await preview.getByTestId("wizard-continue").click();
  for (let i = 0; i < 12 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await preview.getByTestId("wizard-back-to-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-recommended")).toBeVisible(); // preferred profile unchanged
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-fact-diarization")).toHaveText("pyannote");
  await expect(preview.getByTestId("env-profiles").locator("[data-testid^=env-action-]")).toHaveCount(0);
  await shot(preview, "p1-gpu-and-cpu-after-add");
  await preview.getByTestId("nav-transcribe").click();
  expect(await preview.getByTestId("queue-now").innerText()).toBe(jobsBefore);
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveText("2");
  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("input-diarization-normal").locator("input")).toBeChecked(); // default not changed
});

test("P1-2 GPU needs repair + CPU ready → repair: only the GPU changes; Normal start and notes agree", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^構成に問題/);
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  await preview.getByTestId("normal-repair").click();
  await expect(preview.getByTestId("plan-item-repairRuntime")).toContainText("GPU用");
  await expect(preview.getByTestId("plan-agreement")).toHaveCount(0);
  await preview.getByTestId("wizard-approve").click();
  for (let i = 0; i < 6 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await preview.getByTestId("wizard-start-transcribing").click();
  await expect(preview.getByTestId("normal-repair-note")).toHaveCount(0);
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-recommended")).toBeVisible(); // still the preferred one
  await shot(preview, "p1-after-gpu-repair");
});

test("P1-2 CPU ready → add GPU from the card: both profiles ready afterwards", async ({ preview }) => {
  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-setup").click();
  await preview.getByTestId("env-action-addGpu-gpu").click();
  await expect(preview.getByTestId("wizard-device-gpu").locator("input")).toBeChecked();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-approve").click();
  for (let i = 0; i < 12 && (await preview.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await preview.getByTestId("wizard-demo-advance").click();
  await preview.getByTestId("wizard-back-to-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-status-ready")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-ready")).toBeVisible();
});
