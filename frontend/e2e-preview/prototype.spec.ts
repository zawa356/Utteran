import { choose, columns, drag, expect, horizontalOverflow, paneWidth, settled, setup, test } from "./fixtures";

/**
 * UI-3-0 iteration 2 interactions (synthetic data only). Every test also fails on any request
 * outside the dev server, any /api or /next request and any console error (fixtures.ts).
 */

test("labels: prototype / synthetic / fictional environment stated in ja and en", async ({ preview }) => {
  await expect(preview.getByTestId("prototype-banner")).toContainText("プロトタイプ／合成データ／処理しません");
  await expect(preview.getByTestId("prototype-chip")).toContainText("プロトタイプ");
  await expect(preview.getByTestId("normal-start-demo")).toContainText("実処理は開始しません");
  await expect(preview.getByTestId("env-summary").getByTestId("env-demo-chip")).toHaveText("デモ用の架空情報");
  await expect(preview.getByTestId("prototype-toolbar")).toContainText("製品UIではありません");
  await preview.getByTestId("lang-en").click();
  await expect(preview.getByTestId("prototype-banner")).toContainText("Prototype / synthetic data / nothing is processed");
  await expect(preview.locator("html")).toHaveAttribute("lang", "en");
  await expect(preview.getByTestId("normal-start-demo")).toContainText("no processing starts");
  await expect(preview.getByTestId("env-summary").getByTestId("env-demo-chip")).toHaveText("Fictional demo info");
});

test("mode switches the whole IA: Normal = top bar with 3 destinations, Expert = sidebar with 6", async ({ preview }) => {
  await expect(preview.getByTestId("top-nav").getByRole("button")).toHaveCount(3);
  await expect(preview.getByTestId("sidebar")).toHaveCount(0);
  await expect(preview.getByTestId("topnav-transcribe")).toHaveAttribute("aria-current", "page");
  await preview.getByTestId("topnav-setup").click();
  await expect(preview.getByTestId("section-setup")).toBeVisible();
  await preview.getByTestId("topnav-settings").click();
  await expect(preview.getByTestId("section-settings")).toBeVisible();

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("top-nav")).toHaveCount(0);
  await expect(preview.getByTestId("sidebar-list").locator(".v-list-item")).toHaveCount(6);
  await expect(preview.getByTestId("section-settings")).toBeVisible(); // the page is kept
  await preview.getByTestId("nav-queue").click();
  await expect(preview.getByTestId("section-queue")).toBeVisible();
  await preview.getByTestId("mode-normal").click(); // queue is not a Normal destination
  await expect(preview.getByTestId("normal-home")).toBeVisible();
  await expect(preview.getByTestId("topnav-transcribe")).toHaveAttribute("aria-current", "page");
});

test("Normal: one file through start → stages → fail → retry → result; nothing else is shown", async ({ preview }) => {
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-meeting-01.wav");
  await expect(preview.getByTestId("normal-not-started")).toBeVisible();
  await expect(preview.getByTestId("queue-list")).toHaveCount(0);
  await preview.getByTestId("normal-start").click();
  await expect(preview.getByTestId("notice")).toContainText("1件を合成のキューに追加しました");
  await expect(preview.getByTestId("job-state-waiting")).toBeVisible();
  await preview.getByTestId("normal-step").click();
  await preview.getByTestId("normal-step").click();
  await expect(preview.getByTestId("stage-asr")).toHaveAttribute("data-status", "current");
  await preview.getByTestId("normal-fail").click();
  await expect(preview.getByTestId("normal-job-error")).toContainText("demo_failure");
  await preview.getByTestId("normal-retry-demo-job-001").click();
  for (let i = 0; i < 5; i += 1) await preview.getByTestId("normal-step").click(); // waiting → … → succeeded
  await expect(preview.getByTestId("job-state-succeeded")).toBeVisible();
  await expect(preview.getByTestId("stage-output")).toHaveAttribute("data-status", "done");
  await expect(preview.getByTestId("normal-result-demo-job-001")).toContainText("sample-meeting-01.srt");
  await expect(preview.getByTestId("normal-open-folder")).toBeDisabled();
  await preview.getByTestId("normal-open-results").click();
  await expect(preview.getByTestId("section-results")).toBeVisible();
  await expect(preview.getByTestId("mode-expert")).toHaveClass(/v-btn--active/);
});

test("Normal states: empty → choose; unsupported/env reasons; replacing the only file", async ({ preview }) => {
  await setup(preview, { scenario: /^未選択/ });
  await expect(preview.getByTestId("normal-empty")).toBeVisible();
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("ファイルを選ぶと開始できます。");
  await preview.getByTestId("normal-choose").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-call-06.mp3");
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  await preview.getByTestId("normal-choose-other").click(); // the only, unsubmitted file is replaced
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-seminar-07.flac");
  await expect(preview.getByTestId("normal-multi-note")).toHaveCount(0);

  await choose(preview, "toolbar-env", /^未構成の例/);
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("実行環境が利用できない例");
  await expect(preview.getByTestId("env-status-unavailable")).toBeVisible();
  await preview.getByTestId("normal-blocked-setup").click();
  await expect(preview.getByTestId("section-setup")).toBeVisible();

  await setup(preview, { scenario: /^読み込みエラー/ });
  await preview.getByTestId("topnav-transcribe").click();
  await expect(preview.getByTestId("normal-inputs-error")).toHaveAttribute("role", "alert");
  await preview.getByTestId("normal-inputs-retry").click();
  await expect(preview.getByTestId("normal-file-name")).toBeVisible();
});

test("Expert ⇄ Normal keeps every input, selection, override and common edit; nothing is submitted", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル（Expert/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-03").click({ modifiers: ["Control"] });
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("field-language-override").locator("input").click();
  await choose(preview, "input-language-selection", "英語");
  await expect(preview.getByTestId("input-row-in-03-override")).toHaveText("1");

  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("normal-file-name")).toContainText("quarterly-planning"); // last touched
  await expect(preview.getByTestId("normal-multi-note")).toContainText("ほかに4件");
  await expect(preview.getByTestId("config-overrides-note")).toContainText("1つ");
  await choose(preview, "input-language-common", "自動判定");

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("inputs-heading")).toContainText("(5)");
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await expect(preview.getByTestId("inspector-tab-selection")).toHaveAttribute("aria-selected", "true");
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveText("2");
  await expect(preview.getByTestId("expert-queue-empty")).toBeVisible(); // no implicit submission
  await preview.getByTestId("inspector-tab-common").click();
  await expect(preview.getByTestId("input-language-common")).toContainText("自動判定");
  await preview.getByTestId("mode-normal").click();
  await preview.getByTestId("normal-open-expert").click();
  await expect(preview.getByTestId("expert-layout")).toBeVisible();
});

test("Inspector: mixed values across a selection, partial overrides, reset all to common", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル（Expert/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("inputs-select-all").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await expect(preview.getByTestId("inspector-tab-selection")).toContainText("（5）");
  await expect(preview.getByTestId("inspector-target")).toHaveText("5件の入力");
  await expect(preview.getByTestId("field-language-mixed")).toBeVisible();
  await expect(preview.getByTestId("field-language-source")).toHaveText("一部が個別");
  await preview.getByTestId("selection-reset-all").click();
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveCount(0);
  await expect(preview.getByTestId("selection-reset-all")).toBeDisabled();
  await preview.getByTestId("inputs-clear-selection").click();
  await expect(preview.getByTestId("inspector-no-selection")).toBeVisible();
});

test("responsibilities: work pane = submit + summary, Queue page = manage all, Results = finished only", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  const pane = preview.getByTestId("pane-queue");
  await expect(pane.getByTestId("count-running")).toHaveText("2");
  await expect(pane.getByTestId("count-attention")).toHaveText("1");
  await expect(pane.getByTestId("queue-now").locator("li")).toHaveCount(3); // running + needs action only
  await expect(pane.getByTestId("queue-table")).toHaveCount(0);
  await expect(pane.getByTestId("job-detail")).toHaveCount(0);
  await pane.getByTestId("now-retry-demo-job-003").click();
  await expect(pane.getByTestId("count-attention")).toHaveText("0");

  await pane.getByTestId("open-queue-page").click();
  const queue = preview.getByTestId("section-queue");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
  await queue.getByTestId("queue-search").locator("input").fill("lecture");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(1);
  await queue.getByTestId("queue-search").locator("input").fill("");
  await choose(preview, "queue-filter", "待機・実行中");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
  await queue.getByTestId("queue-row-demo-job-002").click();
  await queue.getByTestId("job-detail").getByTestId("job-step").click();
  await expect(queue.getByTestId("job-detail")).toContainText("実行中");
  const stepAll = queue.getByTestId("queue-page-step-all");
  for (let i = 0; i < 12 && (await stepAll.isEnabled()); i += 1) await stepAll.click();

  await preview.getByTestId("nav-results").click();
  const results = preview.getByTestId("section-results");
  await expect(results.getByTestId("results-table").locator("tbody tr")).toHaveCount(4);
  await results.getByTestId("result-row-demo-job-001").click();
  await expect(results.getByTestId("result-detail")).toContainText("sample-meeting-01.json");
  await expect(results.getByTestId("result-detail").getByRole("button", { disabled: true })).toHaveCount(3);
  await expect(results.getByTestId("results-later")).toContainText("S4");
});

test("sidebar: collapse from the top, re-expand from the rail, drag and keyboard resize with bounds", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  const sidebar = preview.getByTestId("sidebar");
  const toggle = preview.getByTestId("nav-toggle");
  const toggleBox = await toggle.boundingBox();
  expect(toggleBox && toggleBox.y).toBeLessThan(100); // at the top, not the bottom
  await toggle.click();
  await expect(sidebar).toHaveAttribute("data-rail", "true");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-label", "サイドバーを広げる");
  await toggle.click();
  await expect(sidebar).toHaveAttribute("data-rail", "false");

  const width = async () => {
    await settled(preview);
    return Math.round((await sidebar.boundingBox())?.width ?? 0);
  };
  expect(await width()).toBe(200);
  await drag(preview, "sidebar-resize", 80);
  expect(await width()).toBe(280);
  await drag(preview, "sidebar-resize", 600);
  expect(await width()).toBe(360); // max
  await drag(preview, "sidebar-resize", -600);
  expect(await width()).toBe(168); // min
  await preview.getByTestId("sidebar-resize").focus();
  await preview.keyboard.press("ArrowRight");
  expect(await width()).toBe(184);
  await preview.keyboard.press("Enter"); // reset
  expect(await width()).toBe(200);
  await preview.getByTestId("sidebar-resize").dblclick();
  expect(await width()).toBe(200);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("panes: drag and keyboard resize, clamped bounds, presets, reset, no overflow", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル（Expert/ });
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(3);
  const before = await paneWidth(preview, "inputs");
  await drag(preview, "resize-inputs", -60);
  expect(await paneWidth(preview, "inputs")).toBe(before - 60);
  await drag(preview, "resize-inputs", -400);
  expect(await paneWidth(preview, "inputs")).toBe(220); // minimum
  const separator = preview.getByTestId("resize-inputs");
  await expect(separator).toHaveAttribute("role", "separator");
  await expect(separator).toHaveAttribute("aria-valuenow", "220");
  await separator.focus();
  await preview.keyboard.press("ArrowRight");
  expect(await paneWidth(preview, "inputs")).toBe(236);
  await preview.keyboard.press("End");
  const max = Number(await separator.getAttribute("aria-valuemax"));
  expect(await paneWidth(preview, "inputs")).toBe(max);
  expect(await paneWidth(preview, "inspector")).toBeGreaterThanOrEqual(340);
  // With the inputs pane at its maximum the right pane cannot grow (the Inspector keeps its minimum).
  const queueAtMax = await paneWidth(preview, "queue");
  await drag(preview, "resize-queue", -80);
  expect(await paneWidth(preview, "queue")).toBe(queueAtMax);
  expect(await paneWidth(preview, "inspector")).toBeGreaterThanOrEqual(340);

  await preview.keyboard.press("Enter"); // the dragged separator keeps focus: Enter resets the panes
  const queueBefore = await paneWidth(preview, "queue");
  await drag(preview, "resize-queue", -50); // dragging left widens the right pane
  expect(await paneWidth(preview, "queue")).toBe(queueBefore + 50);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);

  await preview.getByTestId("preset-wide-inspector").click();
  const wide = await paneWidth(preview, "inspector");
  await preview.getByTestId("panes-reset").click();
  expect(await paneWidth(preview, "inspector")).toBeLessThan(wide);
  await separator.dblclick();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("3 → 2 → 1 columns follow the work-area width; F6 moves panes; no overflow in ja/en × light/dark", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル（Expert/ });
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(3);
  await preview.setViewportSize({ width: 900, height: 660 });
  await expect(preview.getByTestId("sidebar")).toHaveAttribute("data-rail", "true"); // narrow windows start as a rail
  expect(await columns(preview)).toBe(2);
  await expect(preview.getByTestId("resize-inputs")).toBeVisible();
  await preview.getByTestId("nav-toggle").click(); // expanding the sidebar leaves room for one column
  expect(await columns(preview)).toBe(1);
  await expect(preview.getByTestId("expert-pane-switch")).toBeVisible();
  await expect(preview.getByTestId("pane-inspector")).toBeVisible();
  await expect(preview.getByTestId("pane-inputs")).toBeHidden();
  await preview.getByTestId("inspector-tab-common").focus();
  await preview.keyboard.press("F6");
  await expect(preview.getByTestId("pane-queue")).toBeFocused();
  await preview.keyboard.press("Shift+F6");
  await expect(preview.getByTestId("pane-inspector")).toBeFocused();
  await preview.getByTestId("nav-toggle").click();
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
  expect(await columns(preview)).toBe(3);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("keyboard: one tab stop into the dense list, arrows/Space/Enter select; Escape closes the dialog", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル（Expert/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("inputs-remove").focus();
  await preview.keyboard.press("Tab");
  await expect(preview.getByTestId("input-row-in-01")).toBeFocused();
  const outline = await preview.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await preview.keyboard.press("ArrowDown");
  await expect(preview.getByTestId("input-row-in-02")).toBeFocused();
  await preview.keyboard.press("Space");
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("2件選択");
  await preview.keyboard.press("End");
  await expect(preview.getByTestId("input-row-in-05")).toBeFocused();
  await preview.keyboard.press("Enter");
  await expect(preview.getByTestId("inputs-selected-count")).toHaveText("1件選択");
  await expect(preview.getByTestId("input-row-in-05")).toHaveAttribute("aria-selected", "true");
  await preview.getByTestId("inputs-remove").click();
  await expect(preview.getByTestId("remove-dialog-card")).toBeVisible();
  await preview.keyboard.press("Escape");
  await expect(preview.getByTestId("remove-dialog-card")).toBeHidden();
  await preview.getByTestId("inputs-remove").click();
  await preview.getByTestId("remove-confirm").click();
  await expect(preview.getByTestId("inputs-heading")).toContainText("(4)");
});

test("environment mock: GPU / CPU / unconfigured / load failure, re-detect, labelled as fictional", async ({ preview }) => {
  await preview.getByTestId("topnav-setup").click();
  await expect(preview.getByTestId("env-demo-note")).toContainText("架空");
  await expect(preview.getByTestId("env-profile-gpu")).toContainText("large-v3-turbo");
  await expect(preview.getByTestId("env-profile-gpu")).toContainText("CUDA");
  await expect(preview.getByTestId("env-profile-cpu")).toHaveCount(0); // Normal: recommended only
  await expect(preview.getByTestId("env-more-in-expert")).toBeVisible();
  await preview.getByTestId("env-toggle-gpu").click();
  await expect(preview.getByTestId("env-evidence-gpu")).toContainText("（架空）");

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("env-profiles").locator("[data-testid^='env-profile-']")).toHaveCount(2);
  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-recommended")).toBeVisible();
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-status-warning")).toContainText("（例）");
  await preview.getByTestId("env-expand-all").click();
  await expect(preview.getByTestId("env-evidence-cpu")).toContainText("CPUでは処理に時間がかかります");
  await preview.getByTestId("env-density-dense").click();

  await choose(preview, "toolbar-env", /^未構成の例/);
  await expect(preview.getByTestId("env-profile-none")).toContainText("未インストール（例）");
  await choose(preview, "toolbar-env", /^検出の読み込み失敗の例/);
  await expect(preview.getByTestId("env-load-failed")).toHaveAttribute("role", "alert");
  await preview.getByTestId("env-redetect").click();
  await expect(preview.getByTestId("env-checking")).toHaveAttribute("role", "status");
  await preview.getByTestId("env-finish").click();
  await expect(preview.getByTestId("env-load-failed")).toBeVisible();
  for (const chip of await preview.getByTestId("section-setup").getByTestId("env-demo-chip").all()) await expect(chip).toHaveText("デモ用の架空情報");
});

test("palette: green proposal by default, UI-2 blue for comparison; production theme untouched", async ({ preview }) => {
  const primary = () => preview.evaluate(() => getComputedStyle(document.querySelector(".v-application")!).getPropertyValue("--v-theme-primary").trim());
  expect(await primary()).toBe("31,107,79"); // #1f6b4f
  await setup(preview, { theme: "dark" });
  expect(await primary()).toBe("127,209,173"); // #7fd1ad
  await preview.getByTestId("palette-ui2").click();
  expect(await primary()).toBe("141,180,220"); // UI-2 dark #8db4dc
  await setup(preview, { theme: "light" });
  expect(await primary()).toBe("47,93,138"); // UI-2 light #2f5d8a
});
