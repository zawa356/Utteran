import { choose, expect, horizontalOverflow, setup, test } from "./fixtures";

test("prototype / synthetic / not processed is stated in ja and en; nothing but the dev server is used", async ({ preview }) => {
  await expect(preview.getByTestId("prototype-banner")).toContainText("プロトタイプ／合成データ／処理しません");
  await expect(preview.getByTestId("prototype-chip")).toContainText("プロトタイプ");
  await expect(preview.getByTestId("normal-start-demo")).toContainText("実処理は開始しません");
  await preview.getByTestId("lang-en").click();
  await expect(preview.getByTestId("prototype-banner")).toContainText("Prototype / synthetic data / nothing is processed");
  await expect(preview.locator("html")).toHaveAttribute("lang", "en");
  await expect(preview.getByTestId("normal-start-demo")).toContainText("no processing starts");
  await expect(preview.getByTestId("config-unverified")).toHaveText("Demo · unverified");
});

test("normal flow: input → configuration → start → progress (fail, retry) → result", async ({ preview }) => {
  await expect(preview.getByTestId("normal-inputs-summary")).toContainText("5件（対応 4 / 未対応 1）");
  await preview.getByTestId("normal-add-file").click();
  await expect(preview.getByTestId("notice")).toContainText("合成の入力を1件追加しました。");
  await preview.getByTestId("normal-remove-add-01").click();
  await expect(preview.getByTestId("normal-input-add-01")).toHaveCount(0);

  const start = preview.getByTestId("normal-start");
  await expect(start).toContainText("4件");
  await start.click();
  await expect(preview.getByTestId("notice")).toContainText("4件を合成のキューに追加しました");
  await expect(preview.getByTestId("normal-progress-table").locator("tbody tr")).toHaveCount(4);
  await expect(preview.getByTestId("normal-job-demo-job-001")).toContainText("待機");
  await expect(start).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("すべての入力は投入済みです。");

  await preview.getByTestId("queue-step-all").click();
  await preview.getByTestId("queue-step-all").click();
  await expect(preview.getByTestId("normal-job-demo-job-001")).toContainText("文字起こし（ASR）");
  await preview.getByTestId("normal-fail-demo-job-001").click();
  await expect(preview.getByTestId("normal-job-demo-job-001")).toContainText("失敗");
  await preview.getByTestId("normal-retry-demo-job-001").click();
  await expect(preview.getByTestId("normal-job-demo-job-001")).toContainText("待機");

  for (let i = 0; i < 12 && (await preview.getByTestId("queue-step-all").isEnabled()); i += 1) await preview.getByTestId("queue-step-all").click();
  await expect(preview.getByTestId("normal-progress-table").getByTestId("job-state-succeeded")).toHaveCount(4);
  await expect(preview.getByTestId("normal-result-list").locator(".v-list-item")).toHaveCount(4);
  await expect(preview.getByTestId("normal-result-demo-job-001")).toContainText("sample-meeting-01.srt, sample-meeting-01.json");
  await expect(preview.getByTestId("normal-open-folder")).toBeDisabled();
  await preview.getByTestId("normal-open-results").click();
  await expect(preview.getByTestId("section-results")).toBeVisible();
  await expect(preview.getByTestId("results-table").locator("tbody tr")).toHaveCount(4);
});

test("mode switch keeps the selection, overrides and common edits (state lives outside the views)", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-03").click();
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await preview.getByTestId("inspector-tab-selection").click();
  await expect(preview.getByTestId("field-language-source")).toHaveText("共通を継承");
  await expect(preview.getByTestId("input-language-selection").locator('input[role="combobox"]')).toBeDisabled();
  await preview.getByTestId("field-language-override").locator("input").click();
  await expect(preview.getByTestId("field-language-source")).toHaveText("個別");
  await choose(preview, "input-language-selection", "英語");
  await expect(preview.getByTestId("input-row-in-03-override")).toHaveText("個別 1");

  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("config-overrides-note")).toContainText("3件");
  await expect(preview.getByTestId("normal-input-in-03")).toContainText("個別設定あり");
  await choose(preview, "input-language-common", "自動判定");

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await expect(preview.getByTestId("inspector-tab-selection")).toHaveAttribute("aria-selected", "true");
  await expect(preview.getByTestId("input-language-selection")).toContainText("英語");
  await preview.getByTestId("inspector-tab-common").click();
  await expect(preview.getByTestId("input-language-common")).toContainText("自動判定");
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("field-language-reset").click();
  await expect(preview.getByTestId("field-language-source")).toHaveText("共通を継承");
  await expect(preview.getByTestId("input-row-in-03-override")).toHaveCount(0);
  await expect(preview.getByTestId("input-language-selection")).toContainText("自動判定");
});

test("Inspector: mixed values across a selection, partial overrides, reset all to common", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("inputs-select-all").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await expect(preview.getByTestId("inspector-tab-selection")).toContainText("（5）");
  await expect(preview.getByTestId("field-language-mixed")).toBeVisible();
  await expect(preview.getByTestId("field-language-source")).toHaveText("一部が個別");
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveText("個別 2");
  await preview.getByTestId("selection-reset-all").click();
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveCount(0);
  await expect(preview.getByTestId("field-language-mixed")).toHaveCount(0);
  await expect(preview.getByTestId("selection-reset-all")).toBeDisabled();
  await preview.getByTestId("inputs-clear-selection").click();
  await expect(preview.getByTestId("inspector-no-selection")).toBeVisible();
});

test("sidebar and queue: submit, job detail, demo step/fail/retry, other sections offer no fake actions", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("job-detail")).toContainText("sample-meeting-01.wav");
  await expect(preview.getByTestId("job-snapshot")).toContainText("バランス（例） / 日本語");
  await preview.getByTestId("nav-queue").click();
  await expect(preview.getByTestId("section-queue")).toBeVisible();
  await expect(preview.getByTestId("nav-queue")).toHaveClass(/v-list-item--active/);
  const detail = preview.getByTestId("section-queue").getByTestId("job-detail");
  await detail.getByTestId("job-step").click();
  await detail.getByTestId("job-fail").click();
  await expect(detail.getByTestId("job-error")).toContainText("demo_failure");
  await detail.getByTestId("job-retry").click();
  await expect(detail).toContainText("待機");
  await expect(detail).toContainText("2");

  for (const id of ["models", "setup"]) {
    await preview.getByTestId(`nav-${id}`).click();
    await expect(preview.getByTestId(`section-${id}`).getByTestId("later-notice")).toBeVisible();
    await expect(preview.getByTestId(`section-${id}`).getByRole("button")).toHaveCount(0);
  }
  await preview.getByTestId("nav-settings").click();
  await choose(preview, "settings-theme", "ダーク");
  await expect(preview.locator(".v-application")).toHaveClass(/v-theme--dark/);
  await preview.getByTestId("nav-transcribe").click();
  await expect(preview.getByTestId("expert-layout")).toBeVisible(); // the mode was kept
});

test("error, loading and empty states; retry recovers; start explains why it is disabled", async ({ preview }) => {
  await setup(preview, { scenario: /^エラー/ });
  await expect(preview.getByTestId("normal-inputs-error")).toHaveAttribute("role", "alert");
  await expect(preview.getByTestId("normal-inputs-error")).toContainText("demo_unavailable");
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toHaveText("入力一覧を読み込めていないため開始できません。");
  await preview.getByTestId("normal-inputs-retry").click();
  await expect(preview.getByTestId("normal-inputs-table")).toBeVisible();
  await preview.getByTestId("normal-progress-retry").click();
  await expect(preview.getByTestId("normal-progress-empty")).toBeVisible();
  await expect(preview.getByTestId("normal-start")).toBeEnabled();

  await setup(preview, { scenario: /^読み込み中/ });
  await expect(preview.getByTestId("normal-inputs-loading")).toHaveAttribute("role", "status");

  await setup(preview, { scenario: /^空/ });
  await expect(preview.getByTestId("normal-inputs-empty")).toBeVisible();
  await expect(preview.getByTestId("normal-start-blocked")).toHaveText("対応形式の入力を追加してください。");
  await preview.getByTestId("normal-add-folder").click();
  await expect(preview.getByTestId("normal-inputs-table").locator("tbody tr")).toHaveCount(3);
  await expect(preview.getByTestId("normal-start")).toBeEnabled();

  await preview.getByTestId("toolbar-reset").click();
  await expect(preview.getByTestId("notice")).toContainText("デモを初期状態に戻しました。");
  await expect(preview.getByTestId("normal-inputs-table").locator("tbody tr")).toHaveCount(5);
});

test("900×660: one Expert pane with a switcher, F6 moves panes, no horizontal overflow (ja/en, light/dark)", async ({ preview }) => {
  await preview.setViewportSize({ width: 900, height: 660 });
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("expert-pane-switch")).toBeVisible();
  await expect(preview.getByTestId("pane-inspector")).toBeVisible();
  await expect(preview.getByTestId("pane-inputs")).toBeHidden();
  await preview.getByTestId("inspector-tab-common").focus();
  await preview.keyboard.press("F6");
  await expect(preview.getByTestId("pane-queue")).toBeVisible();
  await expect(preview.getByTestId("pane-queue")).toBeFocused();
  await preview.keyboard.press("Shift+F6");
  await expect(preview.getByTestId("pane-inspector")).toBeFocused();
  await preview.getByTestId("pane-switch-inputs").click();
  await expect(preview.getByTestId("input-list")).toBeVisible();
  for (const lang of ["ja", "en"] as const) {
    for (const theme of ["light", "dark"] as const) {
      await setup(preview, { lang, theme });
      for (const mode of ["normal", "expert"] as const) {
        await preview.getByTestId(`mode-${mode}`).click();
        expect(await horizontalOverflow(preview), `${lang}/${theme}/${mode}`).toBeLessThanOrEqual(0);
      }
    }
  }
  await preview.setViewportSize({ width: 1280, height: 820 });
  await expect(preview.getByTestId("pane-inputs")).toBeVisible();
  await expect(preview.getByTestId("pane-queue")).toBeVisible();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("keyboard: Tab reaches the input list, arrows move, Enter toggles; Escape closes the dialog; focus is visible", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("inputs-clear-selection").focus();
  let inList = false;
  for (let i = 0; i < 10 && !inList; i += 1) {
    await preview.keyboard.press("Tab");
    inList = await preview.evaluate(() => Boolean(document.activeElement?.closest('[data-testid="input-list"]')));
  }
  expect(inList).toBe(true);
  const outline = await preview.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await preview.keyboard.press("ArrowDown");
  const focused = await preview.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  expect(focused).toBe("input-row-in-02");
  await preview.keyboard.press("Enter");
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await preview.getByTestId("inputs-remove").click();
  await expect(preview.getByTestId("remove-dialog-card")).toBeVisible();
  await preview.keyboard.press("Escape");
  await expect(preview.getByTestId("remove-dialog-card")).toBeHidden();
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await preview.getByTestId("inputs-remove").click();
  await preview.getByTestId("remove-confirm").click();
  await expect(preview.getByTestId("inputs-heading")).toContainText("(3)");
});
