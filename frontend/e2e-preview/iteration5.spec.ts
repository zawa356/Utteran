import { choose, expect, horizontalOverflow, lowContrastText, readability, settled, setup, shot, test } from "./fixtures";
import type { Page } from "@playwright/test";

/**
 * UI-3-0 iteration 5 (owner memos #25–#36). Synthetic data only; every test also fails on any
 * request outside the dev server, any /api or /next request and any console error (fixtures.ts).
 * Nothing may be downloaded; folder / file pickers are fixed sample lists.
 */
test.beforeEach(async ({ preview }) => {
  preview.on("download", (d) => {
    throw new Error(`unexpected download ${d.suggestedFilename()}`);
  });
});

async function advanceAll(page: Page) {
  for (let i = 0; i < 14 && (await page.getByTestId("wizard-demo-advance").count()) > 0; i += 1) await page.getByTestId("wizard-demo-advance").click();
}

// ---- #30 setup type and model selection (P0) ------------------------------------------------------
test("#30 standard: recommendation shown → change to large-v3 → plan and size follow → done keeps it", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await expect(preview.getByTestId("wizard-method-standard").locator("input")).toBeChecked();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-model-chosen-large-v3-turbo")).toContainText("推奨");
  await preview.getByTestId("wizard-change-model").click();
  await shot(preview, "i5-01-standard-change-model");
  await preview.getByTestId("wizard-model-option-large-v3").locator("input").click();
  await expect(preview.getByTestId("wizard-model-chosen-large-v3")).toBeVisible();
  await expect(preview.getByTestId("wizard-model-chosen-large-v3-turbo")).toHaveCount(0);
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("plan-item-model-large-v3")).toContainText("Whisper large-v3");
  await expect(preview.getByTestId("plan-item-model-large-v3")).toContainText("3.3 GB");
  await expect(preview.getByTestId("plan-item-model-large-v3-turbo")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-review")).not.toContainText("実行すること");
  await shot(preview, "i5-02-review");
  await preview.getByTestId("wizard-approve").click();
  await advanceAll(preview);
  await expect(preview.getByTestId("wizard-done-models")).toHaveText("Whisper large-v3");
  await expect(preview.getByTestId("wizard-done-diarization")).toHaveText("無効");
  await expect(preview.getByTestId("wizard-done")).not.toContainText("誰が話");
  await shot(preview, "i5-03-done");
  await preview.getByTestId("wizard-start-transcribing").click();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-setup").click();
  await expect(preview.getByTestId("env-profile-gpu").getByTestId("env-fact-model")).toHaveText("large-v3");
});

test("#30 custom: Kotoba-Whisper + main model; standard ↔ custom keeps it; CPU disables large-v3; engines not offered", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await preview.getByTestId("wizard-method-custom").locator("input").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-models")).toHaveAttribute("data-method", "custom");
  await preview.getByTestId("wizard-model-check-kotoba-whisper-v2.0").locator("input").click();
  await choose(preview, "wizard-primary-model", "Kotoba-Whisper v2.0");
  await shot(preview, "i5-04-custom-models");
  await preview.getByTestId("wizard-engine").click();
  await expect(preview.getByRole("option", { name: /whisper\.cpp/ })).toHaveClass(/v-list-item--disabled/);
  await preview.keyboard.press("Escape");
  await preview.getByTestId("wizard-method-toggle-standard").click();
  await expect(preview.getByTestId("wizard-model-chosen-kotoba-whisper-v2.0")).toBeVisible(); // nothing lost
  await preview.getByTestId("wizard-method-toggle-custom").click();
  await expect(preview.getByTestId("wizard-model-check-kotoba-whisper-v2.0").locator("input")).toBeChecked();
  // An invalid combination cannot be chosen; a device change flags it and blocks until fixed.
  await preview.getByTestId("wizard-model-check-large-v3").locator("input").click();
  await preview.getByTestId("wizard-device-cpu").locator("input").click();
  await expect(preview.getByTestId("wizard-models-unavailable")).toContainText("Whisper large-v3");
  await expect(preview.getByTestId("wizard-next")).toBeDisabled();
  await preview.getByTestId("wizard-remove-unavailable").click();
  await expect(preview.getByTestId("wizard-model-check-large-v3").locator("input")).toBeDisabled();
  await expect(preview.getByTestId("wizard-next")).toBeEnabled();
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("plan-item-model-kotoba-whisper-v2.0")).toBeVisible();
  await expect(preview.getByTestId("plan-item-runtime")).toContainText("CPU用");
  await preview.getByTestId("wizard-approve").click();
  await advanceAll(preview);
  await expect(preview.getByTestId("wizard-done-models")).toContainText("Kotoba-Whisper v2.0");
});

test("#30 add a model to the ready GPU: only the model is downloaded; GPU, CPU and diarization stay", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-setup").click();
  await preview.getByTestId("env-setup").click();
  await preview.getByTestId("wizard-device-gpu").locator("input").click();
  await expect(preview.getByTestId("wizard-default-kept")).toBeVisible();
  await preview.getByTestId("wizard-change-model").click();
  await preview.getByTestId("wizard-model-option-kotoba-whisper-v2.0").locator("input").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-diarization-kept")).toBeVisible();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-plan").locator("tbody [data-testid^=plan-item-]")).toHaveCount(1);
  await expect(preview.getByTestId("plan-kept-runtime")).toContainText("導入済み");
  await expect(preview.getByTestId("plan-kept-model-large-v3-turbo")).toBeVisible();
  await expect(preview.getByTestId("plan-download")).toHaveText("約1.6 GB");
  await preview.getByTestId("wizard-approve").click();
  await advanceAll(preview);
  await preview.getByTestId("wizard-back-to-setup").click();
  const gpu = preview.getByTestId("env-profile-gpu");
  await expect(gpu.getByTestId("env-fact-model")).toHaveText("large-v3-turbo"); // default unchanged
  await expect(gpu.getByTestId("env-fact-models")).toHaveText("large-v3-turbo, kotoba-whisper-v2.0");
  await expect(gpu.getByTestId("env-fact-diarization")).toHaveText("pyannote");
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-ready")).toBeVisible();
});

// ---- #26 / #28 token and terms on the features page --------------------------------------------------
test("#26 token present / terms missing on the same page blocks next and start; OFF hides both; English labels", async ({ preview }) => {
  await choose(preview, "toolbar-setup", /^初回起動/);
  await preview.getByTestId("wizard-next").click();
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-features")).toContainText("話している人ごとに"); // explained once, here
  await preview.getByTestId("wizard-diarization").locator("input").click();
  await expect(preview.getByTestId("wizard-credential-state")).toHaveAttribute("data-present", "false");
  await shot(preview, "i5-05-diarization-same-page");
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled(); // terms alone are not enough
  await preview.getByTestId("wizard-register-credential").click();
  await expect(preview.getByTestId("wizard-next")).toBeEnabled();
  await preview.getByTestId("wizard-agreement").locator("input").click();
  await expect(preview.getByTestId("wizard-next")).toBeDisabled(); // token alone is not enough
  await preview.getByTestId("wizard-diarization").locator("input").click();
  await expect(preview.getByTestId("wizard-credentials")).toHaveCount(0);
  await expect(preview.getByTestId("wizard-next")).toBeEnabled();
  await preview.getByTestId("lang-en").click();
  await expect(preview.getByTestId("wizard-diarization")).toContainText("Speaker diarization");
  await preview.getByTestId("wizard-next").click();
  await expect(preview.getByTestId("wizard-approve")).toHaveText("Start installation");
  await expect(preview.getByTestId("wizard-review")).not.toContainText("value not shown");
});

// ---- #34 queue clean-up vs. history (P0) ------------------------------------------------------------------
test("#34 clean up twice: finished jobs leave the queue only; results and history keep them; active jobs stay", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-queue").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  for (let i = 0; i < 6 && (await preview.getByTestId("job-step").count()) > 0; i += 1) await preview.getByTestId("job-step").click(); // → succeeded
  await expect(preview.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
  await shot(preview, "i5-06-queue-before-cleanup");
  await preview.getByTestId("queue-clear").click();
  await expect(preview.getByTestId("queue-table").locator("tbody tr")).toHaveCount(2); // failed + succeeded hidden
  await expect(preview.getByTestId("queue-cleaned-note")).toContainText("2件");
  await expect(preview.getByTestId("queue-clear")).toBeDisabled();
  await choose(preview, "queue-filter", "終了したもの");
  await expect(preview.getByTestId("queue-no-match")).toBeVisible();
  await shot(preview, "i5-07-queue-after-cleanup");
  await preview.getByTestId("queue-open-history").click();
  await expect(preview.getByTestId("section-results")).toHaveAttribute("data-tab", "history");
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(2);
  await preview.getByTestId("result-row-demo-job-001").click();
  await expect(preview.getByTestId("result-detail").getByTestId("job-output-names-demo-job-001")).toContainText("sample-meeting-01.srt");
  await shot(preview, "i5-08-history");
  await preview.getByTestId("results-tab-results").click();
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(1);
  // Mode switches and other pages never lose the history.
  await preview.getByTestId("mode-normal").click();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-results").click();
  await preview.getByTestId("results-tab-history").click();
  await choose(preview, "history-filter", "失敗");
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(1);
  // History removal is a confirmation mock: nothing is removed, output files are named as untouched.
  await preview.getByTestId("result-row-demo-job-003").click();
  await preview.getByTestId("result-delete").click();
  await expect(preview.getByTestId("history-delete-card")).toContainText("出力ファイルは削除しません");
  await expect(preview.getByTestId("history-delete-confirm")).toBeDisabled();
  await preview.getByTestId("history-delete-cancel").click();
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(1);
});

// ---- #33 one file, several jobs ----------------------------------------------------------------------------
test("#33 A.wav with model 1, then model 2 as a NEW job: two ids, two snapshots, no overwritten outputs; history keeps both", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("submit-again")).toBeVisible();
  await choose(preview, "input-backend-common", "faster-whisper");
  await choose(preview, "input-model-common", /^large-v3$/);
  await preview.getByTestId("submit-again").click();
  await expect(preview.getByTestId("submit-result")).toContainText("新しいジョブとして追加");
  await expect(preview.getByTestId("submit-result")).toContainText("ジョブIDを付けます");
  await expect(preview.getByTestId("queue-now").locator("li")).toHaveCount(2);
  await expect(preview.getByTestId("now-meta-demo-job-001")).toHaveText("demo-job-001 · バランス");
  await expect(preview.getByTestId("now-meta-demo-job-002")).toHaveText("demo-job-002 · large-v3");
  await shot(preview, "i5-09-same-file-two-jobs");
  await preview.getByTestId("open-queue-page").click();
  await preview.getByTestId("queue-row-demo-job-002").click();
  await expect(preview.getByTestId("job-run-no")).toHaveText("このファイルの2回目");
  await expect(preview.getByTestId("job-output-names-demo-job-002")).toContainText("sample-meeting-01_demo-job-002.srt");
  await expect(preview.getByTestId("job-snapshot")).toContainText("large-v3");
  await preview.getByTestId("queue-row-demo-job-001").click();
  await expect(preview.getByTestId("job-snapshot")).toContainText("バランス"); // the first snapshot is unchanged
  for (let i = 0; i < 8 && (await preview.getByTestId("queue-page-step-all").isEnabled()); i += 1) await preview.getByTestId("queue-page-step-all").click();
  await preview.getByTestId("queue-clear").click();
  await preview.getByTestId("nav-results").click();
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(2);
  // retry ≠ rerun: "run again" makes a third job with the current settings.
  await preview.getByTestId("result-row-demo-job-001").click();
  await preview.getByTestId("result-rerun").click();
  await preview.getByTestId("nav-queue").click();
  await expect(preview.getByTestId("queue-meta-demo-job-003")).toHaveText("demo-job-003 · large-v3");
});

// ---- #35 output folders -----------------------------------------------------------------------------------
test("#35 Normal: one file's folder; Expert: mixed selection, individual / default; frozen in the job; picker touches nothing", async ({ preview }) => {
  const out = preview.getByTestId("normal-file").getByTestId("output-dir");
  await expect(out).toHaveAttribute("data-kind", "default");
  await out.getByTestId("output-dir-change").click();
  await expect(preview.getByTestId("folder-picker-note")).toContainText("表示・作成しません");
  await expect(preview.getByTestId("folder-picker-ok")).toBeDisabled(); // nothing chosen yet: no empty value
  await preview.getByTestId("folder-option-1").locator("input").click();
  await preview.getByTestId("folder-picker-ok").click();
  await expect(out).toHaveAttribute("data-kind", "individual");
  await expect(out.getByTestId("output-dir-value")).toHaveText("D:\\Utteran-demo\\meetings");
  await preview.getByTestId("normal-start").click();
  await out.getByTestId("output-dir-reset").click();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("open-queue-page").click();
  await preview.getByTestId("queue-row-demo-job-001").click();
  await expect(preview.getByTestId("job-output-dir")).toHaveText("D:\\Utteran-demo\\meetings"); // frozen

  await choose(preview, "toolbar-scenario", /^保存先が混在/);
  await preview.getByTestId("nav-transcribe").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("category-output").click();
  const field = preview.getByTestId("category-panel-output").getByTestId("output-dir");
  await expect(field).toHaveAttribute("data-kind", "mixed");
  await shot(preview, "i5-10-output-mixed");
  await field.getByTestId("output-dir-change").click();
  await preview.getByTestId("folder-option-0").locator("input").click();
  await preview.getByTestId("folder-picker-ok").click();
  await expect(field).toHaveAttribute("data-kind", "individual");
  await field.getByTestId("output-dir-reset").click();
  await expect(field).toHaveAttribute("data-kind", "default");
  await expect(field.getByTestId("output-dir-value")).toContainText("D:\\Utteran-demo\\output"); // the default of this scenario
});

test("#35 Settings: default folder; files with their own folder and submitted jobs are not affected", async ({ preview }) => {
  await preview.getByTestId("topnav-settings").click();
  await preview.getByTestId("settings-output-change").click();
  await preview.getByTestId("folder-option-2").locator("input").click();
  await preview.getByTestId("folder-picker-ok").click();
  await expect(preview.getByTestId("settings-output-value")).toHaveText("E:\\demo-share\\transcripts");
  await expect(preview.getByTestId("settings-output-folder").locator("input")).toBeChecked();
  await preview.getByTestId("topnav-transcribe").click();
  await expect(preview.getByTestId("normal-file").getByTestId("output-dir-value")).toContainText("E:\\demo-share\\transcripts");
});

// ---- #36 glossary ---------------------------------------------------------------------------------------
test("#36 paste 120 terms, 1001 refused, duplicates / empty lines named, Unicode kept; TXT import / export mocks", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("category-prompt").click();
  const box = preview.getByTestId("glossary-text").locator("textarea");
  const terms = Array.from({ length: 120 }, (_, i) => (i % 2 ? `DemoTerm-${i}` : `議事録用語${i}`));
  await box.fill([...terms, "", "  ＡＢＣ製品  ", "demoterm-1"].join("\n"));
  await expect(preview.getByTestId("glossary-count")).toHaveText("123行 · 適用される語 121／最大1000");
  await expect(preview.getByTestId("glossary-duplicates")).toContainText("重複 1件");
  await expect(preview.getByTestId("glossary-empty-lines")).toContainText("空行 1件");
  await expect(preview.getByTestId("glossary-trimmed")).toBeVisible();
  await preview.getByTestId("category-asr").click(); // the draft survives a category switch
  await preview.getByTestId("category-prompt").click();
  await expect(preview.getByTestId("glossary")).toHaveAttribute("data-dirty", "true");
  await preview.getByTestId("glossary-apply").click();
  await expect(box).toHaveValue(new RegExp(`^議事録用語0\\nDemoTerm-1[\\s\\S]*ＡＢＣ製品$`));
  await shot(preview, "i5-11-glossary-120");

  await preview.getByTestId("glossary-import").click();
  await preview.getByTestId("glossary-sample-2").locator("input").click();
  await expect(preview.getByTestId("glossary-import-result")).toContainText("適用できません");
  await preview.getByTestId("glossary-import-ok").click();
  await expect(preview.getByTestId("glossary-too-many")).toBeVisible();
  await expect(preview.getByTestId("glossary-apply")).toBeDisabled();
  await preview.getByTestId("glossary-discard").click();
  await preview.getByTestId("glossary-import").click();
  await preview.getByTestId("glossary-sample-0").locator("input").click();
  await expect(preview.getByTestId("glossary-import-preview")).toContainText("BOM");
  await expect(preview.getByTestId("glossary-import-preview")).toContainText("CRLF");
  await preview.getByTestId("glossary-import-append").locator("input").click();
  await preview.getByTestId("glossary-import-ok").click();
  await expect(preview.getByTestId("glossary-count")).toContainText("適用される語 241");
  await preview.getByTestId("glossary-discard").click();
  await preview.getByTestId("glossary-export").click();
  await expect(preview.getByTestId("glossary-export-preview")).toContainText("議事録用語0");
  await preview.getByTestId("glossary-export-save-button").click();
  await expect(preview.getByTestId("glossary-export-save-unavailable")).toContainText("保存できません");
});

test("#36 mixed selection: nothing of one file is shown as the others'; 'Apply to all n' is explicit", async ({ preview }) => {
  await setup(preview, { scenario: /^用語集120語/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("expert-add-file").click();
  await preview.getByTestId("inputs-select-all").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("category-prompt").click();
  await preview.getByTestId("field-glossary-override").locator("input").click();
  await expect(preview.getByTestId("glossary-mixed")).toBeVisible();
  await expect(preview.getByTestId("glossary-text").locator("textarea")).toHaveValue("");
  await preview.getByTestId("glossary-text").locator("textarea").fill("共通語");
  await expect(preview.getByTestId("glossary-apply")).toHaveText("選択中の2件すべてに適用");
});

// ---- #25 / #31 layout -----------------------------------------------------------------------------------
for (const width of [800, 900, 1100, 1280]) {
  test(`#31/#25 ${width}px: Normal card columns by its own width, format chips equal and wrapped, settings labels not touching`, async ({ preview }) => {
    await preview.setViewportSize({ width, height: 820 });
    await settled(preview);
    const chips = preview.getByTestId("input-formats-normal").locator(".v-chip");
    await expect(chips).toHaveCount(5);
    const boxes = await chips.evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height, right: r.right })));
    const widths = boxes.map((b) => Math.round(b.w));
    expect(Math.min(...widths), `chip widths ${widths}`).toBeGreaterThanOrEqual(70);
    for (let i = 1; i < boxes.length; i += 1) if (Math.abs(boxes[i]!.y - boxes[i - 1]!.y) < 2) expect(boxes[i]!.x - boxes[i - 1]!.right).toBeGreaterThanOrEqual(7);
    expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
    if (width === 900 || width === 1280) await shot(preview, `i5-12-normal-card-${width}`);
    for (const lang of ["ja", "en"] as const) {
      await preview.getByTestId(`lang-${lang}`).click();
      await preview.getByTestId("topnav-settings").click();
      await settled(preview);
      for (const id of ["accent", "theme", "language", "output"]) {
        const line = preview.getByTestId(`setline-${id}`);
        const label = await line.locator(".p-setline__label").boundingBox();
        const control = await line.locator(".p-setline__control").boundingBox();
        const gap = control!.y >= label!.y + label!.height - 1 ? control!.y - (label!.y + label!.height) : control!.x - (label!.x + label!.width);
        expect(gap, `${lang} ${id}`).toBeGreaterThanOrEqual(8);
      }
      expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
      await preview.getByTestId("topnav-transcribe").click();
    }
  });
}

for (const c of [
  { accent: "green", theme: "dark", lang: "en" },
  { accent: "blue", theme: "light", lang: "ja" },
] as const) {
  test(`#29 ${c.accent}/${c.theme}/${c.lang}: new screens keep contrast and the readability floor`, async ({ preview }) => {
    await preview.getByTestId(`palette-${c.accent}`).click();
    await setup(preview, { theme: c.theme, lang: c.lang, scenario: /^保存先が混在/ }); // the scenario is chosen before the language switches
    await preview.getByTestId("toolbar-toggle").click();
    const check = async (where: string) => {
      await settled(preview);
      expect(await lowContrastText(preview), where).toEqual([]);
      const r = await readability(preview);
      expect(r.smallText, where).toEqual([]);
      expect(r.smallButtons, where).toEqual([]);
    };
    await preview.getByTestId("mode-expert").click();
    await preview.getByTestId("inspector-tab-selection").click();
    await preview.getByTestId("category-output").click();
    await check("output category");
    await preview.getByTestId("category-prompt").click();
    await check("glossary");
    await preview.getByTestId("nav-setup").click();
    await preview.getByTestId("env-setup").click();
    await preview.getByTestId("wizard-method-toggle-custom").click();
    await check("wizard custom");
    await preview.getByTestId("wizard-close").click();
    await preview.getByTestId("nav-results").click();
    await check("results");
    await shot(preview, `i5-13-theme-${c.accent}-${c.theme}-${c.lang}`);
  });
}

// ---- #32 repair and reset -------------------------------------------------------------------------------
test("#32 Tools → Repair and reset: three separate operations; reset is a confirmation mock that removes nothing", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-maintenance").click();
  const dlg = preview.getByTestId("dialog-maintenance");
  await expect(dlg.getByTestId("maint-repair")).toContainText("設定・履歴・ダウンロード済みモデルはそのまま");
  await dlg.getByTestId("maint-reset-history").locator("input").click();
  await dlg.getByTestId("maint-reset-models").locator("input").click();
  await expect(dlg.getByTestId("maint-reset-running")).toContainText("3件");
  await expect(dlg.getByTestId("maint-reset-confirm")).toBeDisabled();
  await expect(dlg.getByTestId("maint-reset-later")).toContainText("何も消していません");
  await shot(preview, "i5-14-maintenance");
  await dlg.getByTestId("maint-target-gpu").locator("input").click();
  await dlg.getByTestId("maint-repair-open").click();
  await expect(preview.getByTestId("setup-wizard")).toHaveAttribute("data-entry", "repair");
  await expect(preview.getByTestId("wizard-approve")).toHaveText("修復を開始");
  await preview.getByTestId("wizard-close").click();
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("nav-transcribe").click();
  await expect(preview.getByTestId("queue-now").locator("li")).toHaveCount(4); // nothing was reset
});
