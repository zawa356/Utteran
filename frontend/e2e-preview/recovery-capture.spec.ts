import { mkdirSync } from "node:fs";
import type { Page } from "@playwright/test";
import { choose, settled, test } from "./fixtures";

/**
 * UI-3 #96 design recovery: the Design Freeze half of the before/after comparison
 * (e2e/ui3-recovery-capture.v1.spec.ts takes the same states in the product). Runs only with
 * `UI3_RECOVERY_SHOTS=<folder>`; otherwise skipped. Synthetic prototype data only. The review strip
 * (not product UI) is hidden in the images; the prototype banner stays visible on purpose.
 */
const folder = process.env.UI3_RECOVERY_SHOTS ?? "";
test.skip(folder === "", "UI3_RECOVERY_SHOTS is not set");
const dir = `ui3-recovery/${folder}`;

async function capture(page: Page, name: string): Promise<void> {
  mkdirSync(dir, { recursive: true });
  await settled(page);
  await page.waitForTimeout(300);
  await page.screenshot({
    path: `${dir}/freeze-${name}.png`,
    fullPage: true,
    animations: "disabled",
    style: ".v-snackbar, [data-testid='prototype-toolbar'] { visibility: hidden !important; }",
  });
}

test("Normal states", async ({ preview }) => {
  await choose(preview, "toolbar-scenario", /^未選択/);
  await capture(preview, "normal-empty");
  await choose(preview, "toolbar-scenario", /^1ファイル・未開始/);
  await capture(preview, "normal-ready");
  await preview.getByTestId("config-details").click();
  await capture(preview, "normal-details");
  await choose(preview, "toolbar-scenario", /^1ファイル・実行中/);
  await capture(preview, "normal-running");
  await choose(preview, "toolbar-scenario", /^1ファイル・完了/);
  await capture(preview, "normal-finished");
  await preview.setViewportSize({ width: 640, height: 900 });
  await capture(preview, "normal-narrow-640");
});

test("Normal English dark", async ({ preview }) => {
  await preview.getByTestId("lang-en").click();
  await preview.getByTestId("theme-dark").click();
  await capture(preview, "normal-ready-en-dark");
});

test("Expert states", async ({ preview }) => {
  await choose(preview, "toolbar-scenario", /^複数ファイル・未投入/);
  await preview.getByTestId("mode-expert").click();
  await capture(preview, "expert-common");
  await preview.getByTestId("inspector-tab-selection").click();
  await capture(preview, "expert-selection");
  for (const category of ["prompt", "diarization", "output", "advanced"]) {
    await preview.getByTestId(`category-${category}`).click();
    await capture(preview, `expert-selection-${category}`);
  }
  await preview.setViewportSize({ width: 1100, height: 760 });
  await capture(preview, "expert-1100");
  await preview.setViewportSize({ width: 640, height: 760 });
  await capture(preview, "expert-640");
});

test("Setup wizard and page", async ({ preview }) => {
  await preview.getByTestId("topnav-setup").click();
  await capture(preview, "setup");
  for (const [example, name] of [[/^初回起動/, "setup-wizard-welcome"]] as const) {
    await choose(preview, "toolbar-setup", example);
    await capture(preview, name);
  }
  const steps = ["setup-wizard-environment", "setup-wizard-features", "setup-wizard-review"];
  for (const name of steps) {
    await preview.getByTestId("wizard-next").click();
    await capture(preview, name);
  }
});
