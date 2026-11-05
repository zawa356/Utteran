import { appBarOverlaps, choose, columns, drag, expect, gripContrast, horizontalOverflow, paneWidth, readability, settled, setup, test } from "./fixtures";

/**
 * UI-3-0 iteration 3 interactions (synthetic data only). Every test also fails on any request
 * outside the dev server, any /api or /next request and any console error (fixtures.ts).
 */

test("one short prototype line + review strip; product pages carry no demo or connection notes", async ({ preview }) => {
  const banner = preview.getByTestId("prototype-banner");
  await expect(banner).toContainText("試作版");
  await expect(banner).toContainText("合成データ");
  expect((await banner.boundingBox())!.height).toBeLessThanOrEqual(36); // one line at 1280 px
  await expect(preview.getByTestId("prototype-toolbar")).toContainText("製品UIではありません");
  const home = preview.getByTestId("normal-home");
  await expect(home).not.toContainText("デモ");
  await expect(home).not.toContainText("Expert");
  await expect(preview.getByTestId("normal-start")).toHaveText("文字起こしを開始");
  await preview.getByTestId("mode-expert").click();
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  expect(await preview.locator("body").innerText()).not.toMatch(/同期|接続|epoch|\/sync|SSE/);
  await preview.getByTestId("lang-en").click();
  await expect(banner).toContainText("Prototype");
  await expect(banner).toContainText("Synthetic data only");
  await expect(preview.locator("html")).toHaveAttribute("lang", "en");
});

test("mode switches the IA: Normal = 3 destinations in the top bar, Expert = 6 in the sidebar; one shared app menu", async ({ preview }) => {
  await expect(preview.getByTestId("top-nav").getByRole("button")).toHaveCount(3);
  await expect(preview.getByTestId("sidebar")).toHaveCount(0);
  await expect(preview.getByTestId("topnav-transcribe")).toHaveAttribute("aria-current", "page");
  for (const g of ["file", "view", "tools", "help"]) await expect(preview.getByTestId(`menu-${g}`)).toBeVisible();
  await preview.getByTestId("topnav-setup").click();
  await expect(preview.getByTestId("section-setup")).toBeVisible();
  await preview.getByTestId("topnav-settings").click();
  await expect(preview.getByTestId("section-settings")).toBeVisible();

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("top-nav")).toHaveCount(0);
  await expect(preview.getByTestId("sidebar-list").locator(".v-list-item")).toHaveCount(6);
  for (const g of ["file", "view", "tools", "help"]) await expect(preview.getByTestId(`menu-${g}`)).toBeVisible();
  await expect(preview.getByTestId("section-settings")).toBeVisible(); // the page is kept
  await preview.getByTestId("nav-queue").click();
  await expect(preview.getByTestId("section-queue")).toBeVisible();
  await preview.getByTestId("menu-view").click();
  await preview.getByTestId("menu-item-modeNormal").click(); // the View menu switches the mode too
  await expect(preview.getByTestId("normal-home")).toBeVisible();
  await expect(preview.getByTestId("topnav-transcribe")).toHaveAttribute("aria-current", "page");
});

test("Normal: start → stage progress and estimate → fail → retry → result", async ({ preview }) => {
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-meeting-01.wav");
  await expect(preview.getByTestId("queue-now")).toHaveCount(0);
  await preview.getByTestId("normal-start").click();
  await expect(preview.getByTestId("job-state-waiting")).toBeVisible();
  await expect(preview.getByTestId("progress-stage")).toHaveText("順番を待っています");
  await preview.getByTestId("normal-step").click();
  await expect(preview.getByTestId("progress-bar")).toHaveAttribute("data-indeterminate", "true"); // preparing: no number
  await preview.getByTestId("normal-step").click();
  await expect(preview.getByTestId("stage-asr")).toHaveAttribute("data-status", "current");
  await expect(preview.getByTestId("progress-stage")).toHaveText("工程 1/3: 文字起こし");
  await expect(preview.getByTestId("progress-percent")).toHaveText("この工程 0%");
  await expect(preview.getByTestId("progress-eta")).toHaveText("残り時間を推定中");
  await preview.getByTestId("normal-advance").click();
  await preview.getByTestId("normal-advance").click();
  await expect(preview.getByTestId("progress-percent")).toHaveText("この工程 50%");
  await expect(preview.getByTestId("progress-eta")).toContainText("（推定）");
  await expect(preview.getByTestId("progress-elapsed")).toContainText("経過");
  await preview.getByTestId("normal-step").click();
  await preview.getByTestId("normal-step").click(); // → output: reports no progress
  await expect(preview.getByTestId("progress-bar")).toHaveAttribute("data-indeterminate", "true");
  await expect(preview.getByTestId("progress-no-eta")).toBeVisible();
  await expect(preview.getByTestId("progress-percent")).toHaveCount(0);
  await preview.getByTestId("normal-fail").click();
  await expect(preview.getByTestId("normal-job-error")).toContainText("demo_failure");
  await preview.getByTestId("normal-retry-demo-job-001").click();
  for (let i = 0; i < 5; i += 1) await preview.getByTestId("normal-step").click(); // waiting → preparing → ASR → diarization → output → succeeded
  await expect(preview.getByTestId("job-state-succeeded")).toBeVisible();
  await expect(preview.getByTestId("stage-output")).toHaveAttribute("data-status", "done");
  await expect(preview.getByTestId("normal-result-demo-job-001")).toContainText("sample-meeting-01.srt");
  await expect(preview.getByTestId("normal-open-folder")).toBeDisabled();
  await preview.getByTestId("normal-next-file").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-call-06.mp3");
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
});

test("Normal states: empty → choose; unsupported / environment reasons; load error", async ({ preview }) => {
  await setup(preview, { scenario: /^未選択/ });
  await expect(preview.getByTestId("normal-empty")).toBeVisible();
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("ファイルを選ぶと開始できます。");
  await preview.getByTestId("normal-choose").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-call-06.mp3");
  await expect(preview.getByTestId("normal-start")).toBeEnabled();

  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await expect(preview.getByTestId("normal-slow-note")).toContainText("CPU");
  await choose(preview, "toolbar-env", /^未構成の例/);
  await expect(preview.getByTestId("normal-start")).toBeDisabled();
  await expect(preview.getByTestId("normal-start-blocked")).toContainText("実行環境の準備ができていません。");
  await preview.getByTestId("normal-blocked-setup").click();
  await expect(preview.getByTestId("section-setup")).toBeVisible();

  await setup(preview, { scenario: /^読み込みエラー/ });
  await preview.getByTestId("topnav-transcribe").click();
  await expect(preview.getByTestId("normal-inputs-error")).toHaveAttribute("role", "alert");
  await preview.getByTestId("normal-inputs-retry").click();
  await expect(preview.getByTestId("normal-file-name")).toBeVisible();
});

test("Normal 'choose another file' swaps the shown file; Expert inputs, overrides and jobs survive, nothing is submitted", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-04").click(); // waiting job, last touched
  await preview.getByTestId("inspector-tab-selection").click();
  await preview.getByTestId("field-language-override").locator("input").click();
  await choose(preview, "input-language-selection", "英語");
  await expect(preview.getByTestId("input-row-in-04-override")).toHaveText("1");

  await preview.getByTestId("mode-normal").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("lecture-sample-04.mp4"); // last touched
  await expect(preview.getByTestId("input-language-normal")).toContainText("英語"); // the effective value
  const home = preview.getByTestId("normal-home");
  await expect(home).not.toContainText("ほかに");
  await expect(home).not.toContainText("Expert");
  await preview.getByTestId("normal-choose-other").click();
  await expect(preview.getByTestId("normal-file-name")).toHaveText("sample-call-06.mp3");
  await expect(preview.getByTestId("normal-start")).toBeEnabled();
  await expect(home).not.toContainText("lecture-sample-04");

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("inputs-heading")).toHaveText("全6件");
  await expect(preview.getByTestId("input-row-in-04-override")).toHaveText("1");
  await expect(preview.getByTestId("input-row-in-04-state")).toHaveAttribute("title", "待機");
  await expect(preview.getByTestId("input-row-add-01-state")).toHaveAttribute("title", "未投入"); // never submitted implicitly
  await expect(preview.getByTestId("mini-total")).toHaveText("全4件");
});

test("Expert left → right: the selection count matches in all three panes; submit shows the new jobs at once", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-01").click();
  await preview.getByTestId("input-row-in-02").click({ modifiers: ["Control"] });
  await preview.getByTestId("input-row-in-03").click({ modifiers: ["Control"] });
  await expect(preview.getByTestId("pane-inputs-heading-meta")).toHaveText("選択中: 3件");
  await expect(preview.getByTestId("pane-inspector-heading-meta")).toHaveText("対象: 3件");
  await expect(preview.getByTestId("inspector-tab-selection")).toHaveText("選択した3件");
  await expect(preview.getByTestId("submit-selected")).toHaveText("選択した3件をキューへ投入");
  await expect(preview.getByTestId("expert-queue-empty")).toBeVisible();
  for (const [i, name] of [["1", "ファイル"], ["2", "処理設定"], ["3", "キュー投入"]] as const) {
    await expect(preview.locator(".p-panehead").nth(Number(i) - 1)).toContainText(`${i}${name}`);
  }

  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("submit-result")).toHaveText("3件をキューに追加しました。");
  const mini = preview.getByTestId("queue-now").locator("li");
  await expect(mini).toHaveCount(3);
  for (const id of ["demo-job-001", "demo-job-002", "demo-job-003"]) await expect(preview.getByTestId(`now-${id}`)).toHaveAttribute("data-new", "true");
  await expect(preview.getByTestId("submit-selected")).toBeDisabled();
  await expect(preview.getByTestId("mini-total")).toHaveText("全3件");

  // Mixed selection: unsupported and already-submitted files are named, not silently dropped.
  await preview.getByTestId("input-row-in-03").click();
  await preview.getByTestId("input-row-in-04").click({ modifiers: ["Control"] });
  await preview.getByTestId("input-row-in-05").click({ modifiers: ["Control"] });
  await expect(preview.getByTestId("submit-selected")).toHaveText("選択した1件をキューへ投入");
  await expect(preview.getByTestId("submit-partial")).toContainText("2件");
  await preview.getByTestId("submit-selected").click();
  await expect(preview.getByTestId("submit-result")).toContainText("1件をキューに追加しました。");
  await expect(preview.getByTestId("submit-result")).toContainText("1件は投入済み");
  await expect(preview.getByTestId("submit-result")).toContainText("1件は未対応の形式");
  await expect(preview.getByTestId("now-demo-job-004")).toHaveAttribute("data-new", "true");
  await preview.getByTestId("input-row-in-05").click();
  await expect(preview.getByTestId("submit-blocked")).toHaveText("選択したファイルは未対応の形式です。");

  await preview.getByTestId("open-queue-page").click();
  await expect(preview.getByTestId("section-queue")).toBeVisible();
  await expect(preview.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
});

test("Expert short queue: at most five rows, stage progress per row, the rest behind 'Open queue'", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("expert-add-folder").click(); // +3 inputs
  await preview.getByTestId("submit-all").click();
  const mini = preview.getByTestId("queue-now").locator("li");
  await expect(mini).toHaveCount(5);
  await expect(preview.getByTestId("open-queue-page")).toHaveText("キューを開く（ほか2件）");
  await expect(preview.getByTestId("progress-demo-job-001")).toContainText("話者分離 50%");
  await expect(preview.getByTestId("progress-demo-job-001")).toContainText("（推定）");
  await expect(preview.getByTestId("pane-queue").getByTestId("queue-table")).toHaveCount(0); // no duplicate management UI
  await preview.getByTestId("queue-step-all").click();
  await expect(preview.getByTestId("now-demo-job-005")).toBeVisible();
});

test("job status: nothing shown when current; 'checking' and 'unavailable' never claim a job stopped", async ({ preview }) => {
  await setup(preview, { scenario: /^1ファイル・実行中/ });
  await expect(preview.getByTestId("status-checking")).toHaveCount(0);
  await expect(preview.getByTestId("status-unavailable")).toHaveCount(0);
  await choose(preview, "toolbar-sync", /^確認に時間がかかっている例/);
  await expect(preview.getByTestId("status-checking")).toHaveText("処理状況を確認しています…");
  await expect(preview.getByTestId("job-state-running")).toBeVisible();
  await choose(preview, "toolbar-sync", /^取得できない例/);
  const warn = preview.getByTestId("status-unavailable");
  await expect(warn).toContainText("処理状況を取得できません");
  await expect(warn).toContainText("止まったとは限りません");
  await expect(preview.getByTestId("job-state-running")).toBeVisible(); // last known state stays
  await expect(preview.getByTestId("normal-job-error")).toHaveCount(0);
  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("pane-queue").getByTestId("status-unavailable")).toBeVisible();
  await expect(preview.getByTestId("sidebar")).not.toContainText("処理状況");
  await preview.getByTestId("pane-queue").getByTestId("status-retry").click();
  await expect(preview.getByTestId("status-unavailable")).toHaveCount(0);
});

test("Inspector: mixed values across a selection, partial overrides, reset all to common", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("inputs-select-all").click();
  await preview.getByTestId("inspector-tab-selection").click();
  await expect(preview.getByTestId("inspector-tab-selection")).toHaveText("選択した5件");
  await expect(preview.getByTestId("inspector-target")).toHaveText("5件のファイル");
  await expect(preview.getByTestId("field-language-mixed")).toBeVisible();
  await expect(preview.getByTestId("field-language-source")).toHaveText("一部が個別");
  await preview.getByTestId("selection-reset-all").click();
  await expect(preview.getByTestId("input-row-in-02-override")).toHaveCount(0);
  await expect(preview.getByTestId("selection-reset-all")).toBeDisabled();
  await preview.getByTestId("inputs-clear-selection").click();
  await expect(preview.getByTestId("inspector-no-selection")).toBeVisible();
  await expect(preview.getByTestId("submit-blocked")).toHaveText("左の一覧でファイルを選んでください。");
});

test("Queue and Results pages keep their own responsibilities", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("now-retry-demo-job-003").click();
  await preview.getByTestId("open-queue-page").click();
  const queue = preview.getByTestId("section-queue");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
  await queue.getByTestId("queue-search").locator("input").fill("lecture");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(1);
  await queue.getByTestId("queue-search").locator("input").fill("");
  await choose(preview, "queue-filter", "待機・実行中");
  await expect(queue.getByTestId("queue-table").locator("tbody tr")).toHaveCount(4);
  await queue.getByTestId("queue-row-demo-job-001").click();
  await expect(queue.getByTestId("job-detail").getByTestId("progress-percent")).toHaveText("この工程 50%");
  await queue.getByTestId("job-detail").getByTestId("job-advance").click();
  await expect(queue.getByTestId("job-detail").getByTestId("progress-percent")).toHaveText("この工程 75%");
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
  expect(toggleBox && toggleBox.y).toBeLessThan(110); // at the top, not the bottom
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
  const handle = await preview.getByTestId("sidebar-resize").boundingBox();
  expect(Math.abs(handle!.x + handle!.width - 200)).toBeLessThanOrEqual(2); // on the sidebar's right edge
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

test("panes: 22/53/25 default candidate, drag and keyboard resize, clamped bounds, presets, View-menu reset", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(3);
  const initial = await paneWidth(preview, "inputs");
  expect(initial).toBeLessThan(await paneWidth(preview, "queue") + 10);
  expect(await paneWidth(preview, "inspector")).toBeGreaterThan(2 * initial);
  await drag(preview, "resize-inputs", 60);
  expect(await paneWidth(preview, "inputs")).toBe(initial + 60);
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
  const queueAtMax = await paneWidth(preview, "queue");
  await drag(preview, "resize-queue", -80); // the Inspector keeps its minimum
  expect(await paneWidth(preview, "queue")).toBe(queueAtMax);

  await preview.keyboard.press("Enter"); // the dragged separator keeps focus: Enter resets the panes
  expect(await paneWidth(preview, "inputs")).toBe(initial);
  const queueBefore = await paneWidth(preview, "queue");
  await drag(preview, "resize-queue", -50); // dragging left widens the right pane
  expect(await paneWidth(preview, "queue")).toBe(queueBefore + 50);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);

  await preview.getByTestId("preset-wideInputs").click();
  expect(await paneWidth(preview, "inputs")).toBeGreaterThan(initial + 50);
  await preview.getByTestId("menu-view").click();
  await preview.getByTestId("menu-item-resetPanes").click();
  expect(await paneWidth(preview, "inputs")).toBe(initial);
  await separator.dblclick();
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("resize handles are visible at rest: grip ≥ 3:1 against its background in light and dark", async ({ preview }) => {
  await preview.getByTestId("mode-expert").click();
  for (const theme of ["light", "dark"] as const) {
    await setup(preview, { theme });
    for (const id of ["resize-inputs", "resize-queue", "sidebar-resize"]) {
      expect(await gripContrast(preview, id), `${theme} ${id}`).toBeGreaterThanOrEqual(3);
    }
  }
});

test("3 → 2 → 1 columns follow the work-area width; F6 moves panes; no overflow in ja/en × light/dark", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  expect(await columns(preview)).toBe(3);
  await preview.setViewportSize({ width: 900, height: 660 });
  await expect(preview.getByTestId("sidebar")).toHaveAttribute("data-rail", "true"); // narrow windows start as a rail
  expect(await columns(preview)).toBe(2);
  await expect(preview.getByTestId("resize-inputs")).toBeVisible();
  await preview.getByTestId("nav-toggle").click(); // expanding the sidebar leaves room for one column
  expect(await columns(preview)).toBe(1);
  await expect(preview.getByTestId("expert-pane-switch")).toBeVisible();
  await expect(preview.getByTestId("pane-switch-inputs")).toHaveText("1 ファイル");
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
        expect(await appBarOverlaps(preview), `${lang}/${theme}/${mode}`).toEqual([]);
      }
    }
  }
  await preview.setViewportSize({ width: 1280, height: 820 });
  expect(await columns(preview)).toBe(3);
  expect(await horizontalOverflow(preview)).toBeLessThanOrEqual(0);
});

test("keyboard: one tab stop into the dense list, arrows/Space/Enter select; Escape closes the dialog", async ({ preview }) => {
  await setup(preview, { scenario: /^複数ファイル・未投入/ });
  await preview.getByTestId("mode-expert").click();
  const meta = preview.getByTestId("pane-inputs-heading-meta");
  await preview.getByTestId("inputs-remove").focus();
  await preview.keyboard.press("Tab");
  await expect(preview.getByTestId("input-row-in-01")).toBeFocused();
  const outline = await preview.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await preview.keyboard.press("ArrowDown");
  await expect(preview.getByTestId("input-row-in-02")).toBeFocused();
  await preview.keyboard.press("Space");
  await expect(meta).toHaveText("選択中: 2件");
  await preview.keyboard.press("End");
  await expect(preview.getByTestId("input-row-in-05")).toBeFocused();
  await preview.keyboard.press("Enter");
  await expect(meta).toHaveText("選択中: 1件");
  await expect(preview.getByTestId("input-row-in-05")).toHaveAttribute("aria-selected", "true");
  await preview.getByTestId("inputs-remove").click();
  await expect(preview.getByTestId("remove-dialog-card")).toBeVisible();
  await preview.keyboard.press("Escape");
  await expect(preview.getByTestId("remove-dialog-card")).toBeHidden();
  await preview.getByTestId("inputs-remove").click();
  await preview.getByTestId("remove-confirm").click();
  await expect(preview.getByTestId("inputs-heading")).toHaveText("全4件");
});

test("environment: one layout, no density / expand-all; Normal brief, Expert candidates with details; fictional stated once", async ({ preview }) => {
  await preview.getByTestId("topnav-setup").click();
  const page = preview.getByTestId("section-setup");
  await expect(preview.getByTestId("env-demo-note")).toContainText("架空");
  await expect(page.getByText("デモ用の架空", { exact: false })).toHaveCount(1);
  await expect(preview.getByTestId("env-density")).toHaveCount(0);
  await expect(preview.getByTestId("env-expand-all")).toHaveCount(0);
  await expect(preview.getByTestId("env-profile-gpu")).toContainText("large-v3-turbo");
  await expect(preview.getByTestId("env-profile-gpu")).not.toContainText("float16"); // brief: key facts only
  await expect(preview.getByTestId("env-profile-cpu")).toHaveCount(0); // Normal: recommended only
  await expect(preview.getByTestId("env-toggle-gpu")).toHaveCount(0);

  await preview.getByTestId("mode-expert").click();
  await expect(preview.getByTestId("env-profiles").locator("[data-testid^='env-profile-']")).toHaveCount(2);
  await expect(preview.getByTestId("env-profile-gpu")).toContainText("float16");
  await choose(preview, "toolbar-env", /^CPUのみの例/);
  await expect(preview.getByTestId("env-profile-cpu").getByTestId("env-recommended")).toBeVisible();
  await preview.getByTestId("env-toggle-cpu").click();
  await expect(preview.getByTestId("env-evidence-cpu")).toContainText("CPUでは時間がかかります");
  await expect(preview.getByTestId("env-evidence-gpu")).toHaveCount(0); // each candidate opens on its own

  await choose(preview, "toolbar-env", /^未構成の例/);
  await expect(preview.getByTestId("env-profile-none")).toContainText("未インストール");
  await choose(preview, "toolbar-env", /^検出の読み込み失敗の例/);
  await expect(preview.getByTestId("env-load-failed")).toHaveAttribute("role", "alert");
  await preview.getByTestId("env-redetect").click();
  await expect(preview.getByTestId("env-checking")).toHaveAttribute("role", "status");
  await preview.getByTestId("env-finish").click();
  await expect(preview.getByTestId("env-load-failed")).toBeVisible();
});

test("app menu: Tools opens sample logs and a diagnostics outline, Help shows 'About'; nothing is read or written", async ({ preview }) => {
  await setup(preview, { scenario: /^1ファイル・失敗/ });
  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-systemLog").click();
  const dialog = preview.getByTestId("dialog-systemLog");
  await expect(dialog).toContainText("システムログ");
  await expect(dialog.getByTestId("dialog-demo-chip")).toBeVisible();
  await expect(dialog.getByTestId("log-lines")).toContainText("値は記録しません");
  await preview.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-jobLog").click();
  const jobLog = preview.getByTestId("dialog-jobLog");
  await expect(jobLog.getByTestId("log-lines")).toContainText("失敗しました（コード demo_failure）");
  await expect(jobLog.getByTestId("log-lines")).not.toContainText("sample-meeting-01"); // no file names in logs
  await jobLog.getByTestId("dialog-close").click();

  await preview.getByTestId("menu-tools").click();
  await preview.getByTestId("menu-item-diagnostics").click();
  const diag = preview.getByTestId("dialog-diagnostics");
  await expect(diag.getByTestId("diag-excluded")).toContainText("フルパス");
  await expect(diag.getByTestId("diag-excluded")).toContainText("文字起こしの本文");
  await expect(diag.getByTestId("diag-export")).toBeDisabled();
  await diag.getByTestId("dialog-ok").click();

  await preview.getByTestId("menu-help").click();
  await expect(preview.getByTestId("menu-item-repo")).toHaveAttribute("href", "https://github.com/zawa356/Utteran");
  await preview.getByTestId("menu-item-about").click();
  const about = preview.getByTestId("dialog-about");
  await expect(about.getByTestId("about-version")).toHaveText("0.1.43（未リリースの開発版）");
  await expect(about.getByTestId("about-dev-chip")).toBeVisible();
  await expect(about).toContainText("MIT License");
  await expect(about.getByTestId("about-repo")).toHaveAttribute("href", "https://github.com/zawa356/Utteran");
  for (const href of await about.locator("a").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).href))) {
    expect(href.startsWith("https://github.com/zawa356/Utteran")).toBe(true); // no unverified site
  }
  await about.getByTestId("dialog-ok").click();

  await preview.getByTestId("menustyle-button").click();
  await expect(preview.getByTestId("menu-file")).toHaveCount(0);
  await preview.getByTestId("menu-button").click();
  await expect(preview.getByTestId("menu-item-about")).toBeVisible();
  await preview.keyboard.press("Escape");
});

test("settings: Normal = a few app-wide items; Expert = categories; nothing per-job; unsaved state stated", async ({ preview }) => {
  await preview.getByTestId("topnav-settings").click();
  const page = preview.getByTestId("section-settings");
  await expect(page).toHaveAttribute("data-variant", "normal");
  await expect(preview.getByTestId("settings-status")).toContainText("保存されません");
  await expect(preview.getByTestId("settings-categories")).toHaveCount(0);
  await expect(preview.getByTestId("settings-output").locator("input")).toHaveValue("入力ファイルと同じフォルダ");
  await expect(page).not.toContainText("文字起こしモデル"); // per-job settings live in the Inspector
  await choose(preview, "settings-theme", "ダーク");
  await expect(preview.locator(".v-application")).toHaveClass(/v-theme--greenDark/);

  await preview.getByTestId("mode-expert").click();
  await expect(page).toHaveAttribute("data-variant", "expert");
  await expect(preview.getByTestId("settings-categories").locator(".v-list-item")).toHaveCount(4);
  await preview.getByTestId("settings-cat-advanced").click();
  await expect(preview.getByTestId("settings-token").locator("input")).toHaveValue("設定済み（値は表示しません）");
  await expect(preview.getByTestId("settings-body-advanced")).toContainText("同時実行数");
  await preview.getByTestId("settings-cat-presets").click();
  await expect(preview.getByTestId("settings-presets").locator(".v-list-item")).toHaveCount(3);
  await expect(preview.getByTestId("settings-body-presets").getByRole("button", { disabled: true })).toHaveCount(2);
  await preview.getByTestId("settings-cat-logs").click();
  await preview.getByTestId("settings-open-diagnostics").click();
  await expect(preview.getByTestId("dialog-diagnostics")).toBeVisible();
  await preview.getByTestId("dialog-ok").click();
  await expect(page).not.toContainText("出力形式");
});

test("readability floor: no product text under 13 px, no button under 36 px (Normal, Expert, pages, ja/en)", async ({ preview }) => {
  const check = async (where: string) => {
    const r = await readability(preview);
    expect(r.smallText, `${where} text`).toEqual([]);
    expect(r.smallButtons, `${where} buttons`).toEqual([]);
  };
  await setup(preview, { scenario: /^1ファイル・実行中/ });
  await check("normal running");
  await setup(preview, { scenario: /^複数ファイル・処理中/ });
  await preview.getByTestId("mode-expert").click();
  await preview.getByTestId("input-row-in-02").click({ modifiers: ["Control"] });
  await preview.getByTestId("inspector-tab-selection").click();
  await check("expert selection");
  for (const nav of ["queue", "results", "setup", "settings"] as const) {
    await preview.getByTestId(`nav-${nav}`).click();
    await check(`expert ${nav}`);
  }
  await setup(preview, { lang: "en" });
  await preview.getByTestId("nav-transcribe").click();
  await check("expert en");
  await preview.getByTestId("mode-normal").click();
  await check("normal en");
});

test("top bar at large zoom / narrow windows: destinations fold into a menu, nothing overlaps or shrinks", async ({ preview }) => {
  for (const [w, h] of [[1024, 656], [853, 547], [640, 410]] as const) {
    await preview.setViewportSize({ width: w, height: h });
    await settled(preview);
    expect(await appBarOverlaps(preview), `${w}`).toEqual([]);
    expect(await horizontalOverflow(preview), `${w}`).toBeLessThanOrEqual(0);
    await expect(preview.getByTestId("menu-button")).toBeVisible(); // the menu bar folds below 1100 px
  }
  await expect(preview.getByTestId("topnav-folded")).toHaveText(/文字起こし/);
  await preview.getByTestId("topnav-folded").click();
  await preview.getByTestId("topnav-settings").click();
  await expect(preview.getByTestId("section-settings")).toBeVisible();
  await expect(preview.getByTestId("topnav-folded")).toHaveText(/設定/);
  const r = await readability(preview);
  expect(r.smallText).toEqual([]);
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
