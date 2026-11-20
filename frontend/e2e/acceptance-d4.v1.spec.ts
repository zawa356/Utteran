import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { choose, expect, SESSION, SESSION_HEADER } from "./fixtures";
import { test } from "./isolatedV1";
import { THREE_COLUMNS_AT, TWO_COLUMNS_AT } from "../src/domain/paneLayout";

async function capture(page: Page, name: string): Promise<void> {
  if (!process.env.D4_SCREENSHOTS) return;
  await page.screenshot({ path: `test-results/d4-review/${name}.png`, animations: "disabled" });
}

async function assertPanes(page: Page): Promise<number> {
  const grid = page.getByTestId("expert-panes");
  const workspace = page.getByTestId("expert-workspace");
  const visible = grid.locator("[data-pane]:visible");
  const geometry = async () => {
    const width = (await grid.boundingBox())!.width;
    const columns = width >= THREE_COLUMNS_AT ? 3 : width >= TWO_COLUMNS_AT ? 2 : 1;
    const panes = await visible.evaluateAll(nodes => nodes.map(node => {
      const rect = node.getBoundingClientRect();
      return { top: rect.top, spill: node.scrollWidth - node.clientWidth };
    }));
    const actual = Number(await workspace.getAttribute("data-columns"));
    return { columns, actual, shown: panes.length, tops: new Set(panes.map(pane => pane.top)).size, spill: Math.max(0, ...panes.map(pane => pane.spill)) };
  };
  await expect.poll(async () => {
    const { columns, actual, shown, tops, spill } = await geometry();
    return { matchesWidth: actual === columns && shown === columns, tops, spill };
  }).toEqual({ matchesWidth: true, tops: 1, spill: 0 });
  return (await geometry()).columns;
}

async function noPageOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => {
    const { documentElement: root } = document;
    return root.scrollWidth - root.clientWidth;
  })).toBeLessThanOrEqual(0);
}

const modes = [
  { width: 1920, height: 1080, dpr: 1 },
  { width: 1440, height: 900, dpr: 1.25 },
  { width: 1100, height: 760, dpr: 1.5 },
  { width: 640, height: 660, dpr: 2 },
] as const;
for (const [i, { width, height, dpr }] of modes.entries()) {
  test.describe(`D4-C width ${width} DPR ${dpr}`, () => {
    test.use({ viewport: { width, height }, deviceScaleFactor: dpr });
    for (const accent of ["blue", "green"] as const) {
      const language = (accent === "green") === (i % 2 === 0) ? "en" : "ja";
      const theme = (accent === "green") === (i < 2) ? "dark" : "light";
      test(`${language} ${theme} ${accent} retains file and lets the keyboard reach all panes`, async ({ app, request }) => {
        expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data: { language, theme } })).status()).toBe(200);
        await app.reload();
        const mode = app.getByTestId("app-ready");
        if (width < 1000) await app.getByTestId("top-nav-menu").click();
        await app.getByTestId("nav-settings").click();
        await choose(app, "settings-accent", accent === "green" ? /^(Green|緑)$/ : /^(Blue|青)$/);
        await expect(mode).toHaveAttribute("data-accent", accent);
        await expect(mode).toHaveClass(new RegExp(accent === "green" ? `v-theme--green${theme === "dark" ? "Dark" : "Light"}` : `v-theme--${theme}`));
        await expect(app.locator("html")).toHaveAttribute("lang", language);
        await capture(app, `product-settings-${width}-${language}-${theme}-${accent}`);
        if (width < 1000) await app.getByTestId("top-nav-menu").click();
        await app.getByTestId("nav-transcribe").click();
        const file = `/synthetic/${"会議".repeat(32)}-${"a".repeat(72)}.wav`;
        await app.getByTestId("tx-input-path").locator("input").fill(file);
        await app.getByTestId("tx-input-add-typed").click();
        await expect(app.getByTestId("tx-input-current")).toHaveText(file);
        await expect(app.getByTestId("v1-submit")).toBeDisabled();
        await noPageOverflow(app);
        await capture(app, `product-normal-${width}-${language}-${theme}-${accent}`);
        await app.getByTestId("mode-expert").click();
        await assertPanes(app);
        await capture(app, `product-expert-${width}-${language}-${theme}-${accent}`);
        const panes = app.getByTestId("expert-panes").locator("[data-pane]:visible");
        await panes.first().focus();
        await panes.first().dispatchEvent("keydown", { key: "F6", isComposing: true });
        await expect(panes.first()).toBeFocused();
        for (let step = 0; step < 3; step++) {
          await app.keyboard.press("F6");
          await expect(app.locator("[data-pane]:focus")).toBeVisible();
          await assertPanes(app);
        }
        // Width and layout are CSS pixels even when rasterized at a higher DPR.
        expect(await app.evaluate(() => window.devicePixelRatio)).toBe(dpr);
        await noPageOverflow(app);
        await app.getByTestId("mode-normal").click();
        await expect(app.getByTestId("tx-input-current")).toHaveText(file);
      });
    }
  });
}

test("D4-C shows the real B5/B7 lifecycle from submission to results", async ({ app, backend }) => {
  const call = (id: string) => app.getByTestId(id);
  const writes: string[] = [];
  app.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${url.pathname}`);
  });
  await expect(call("v1-presets-empty")).toBeVisible();
  await capture(app, "product-normal-empty-ja-light-blue-1280");
  await call("v1-preset-create-open").click();
  await call("v1-preset-name").locator("input").fill("Synthetic review");
  await call("v1-preset-model").locator("input").fill("large-v3-turbo");
  await call("v1-preset-check").click();
  await call("v1-preset-save").click();
  await call("v1-policy-create-open").click();
  await call("v1-policy-name").locator("input").fill("Synthetic CPU");
  await call("v1-policy-check").click();
  await call("v1-policy-save").click();
  await call("v1-output-path").locator("input").fill(`${backend.root}/out`);
  await call("mode-expert").click();
  for (const name of ["meeting.wav", "other.wav"]) {
    await call("tx-input-path").locator("input").fill(`${backend.root}/in/${name}`);
    await call("tx-input-add-typed").click();
  }
  await call("tx-list-select-all").click();
  await assertPanes(app);
  await capture(app, "product-expert-1280-ja-light-blue");
  for (const width of [1100, 640]) {
    await app.setViewportSize({ width, height: width === 1100 ? 760 : 660 });
    await assertPanes(app);
    await capture(app, `product-expert-${width}-ja-light-blue`);
  }
  await app.setViewportSize({ width: 1280, height: 820 });
  await assertPanes(app);
  await call("v1-batch-check").click();
  await expect(call("v1-batch-resolved")).toBeVisible();
  await call("v1-batch-submit").click();
  await expect(call("v1-batch-accepted")).toBeVisible();
  expect(writes.filter(line => line === "POST /api/v1/jobs")).toHaveLength(1);
  await call("nav-queue").click();
  await expect(call("queue-row")).toHaveCount(2);
  await call("queue-row").first().click();
  await expect(call("queue-detail")).toBeVisible();
  await capture(app, "product-queue-ja-light-blue-1280");
  const dispatch = call("queue-dispatch");
  await expect(dispatch).toBeEnabled();
  if (await call("queue-dispatch-paused").isVisible()) await dispatch.click();
  await call("nav-results").click();
  await expect(call("v1-board-row").filter({ has: call("results-select") })).toHaveCount(2, { timeout: 20_000 });
  await call("results-select").first().click();
  await call("v1-artifacts-load").click();
  await expect(call("v1-artifacts-external-blocked")).toBeVisible();
  await capture(app, "product-results-ja-light-blue-1280");
});

test("D4-C confirmations and connection failures preserve the actual job state", async ({ app, backend, request }) => {
  const auth = { Cookie: `utteran_gui_session=${SESSION}` };
  const post = async (endpoint: string, data: object, status: number) => {
    const response = await request.post(`/api/v1/${endpoint}`, {
      headers: { ...auth, Origin: backend.url, "Idempotency-Key": `d4-review-fixture-${endpoint}` },
      data,
    });
    expect(response.status()).toBe(status);
    return response.json();
  };
  // Seed the one job through the real backend. The separate lifecycle test drives submission via the UI.
  const settings = JSON.parse(readFileSync(new URL("../src/domain/v1/presetTemplate.json", import.meta.url), "utf8"));
  const preset = await post("presets", { schema_version: 1, name: "Synthetic review", settings }, 201);
  const policy = await post("execution-policies", {
    schema_version: 1, name: "Synthetic CPU", mode: "auto", fixed_candidate_id: null,
    allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null,
    fallback: "none", execution_deadline_seconds: null,
  }, 201);
  const editing = {
    schema_version: 1,
    preset_ref: { id: preset.id, revision: preset.revision },
    policy_ref: { id: policy.id, revision: policy.revision },
    common_overrides: [],
    files: [{ client_file_id: "f-d4review000000000000000000", input_path: `${backend.root}/in/meeting.wav`, output_dir: `${backend.root}/out`, overrides: [] }],
  };
  const resolution = await post("execution-resolutions", editing, 201);
  await post("jobs", { schema_version: 1, editing_session: editing, resolution_token: resolution.token, resume_mode: "resume" }, 202);
  const call = (id: string) => app.getByTestId(id);
  await call("mode-expert").click();
  await call("nav-queue").click();
  const dispatch = call("queue-dispatch");
  await expect(dispatch).toBeEnabled();
  if (await call("queue-dispatch-paused").isVisible()) await dispatch.click();
  await call("nav-results").click();
  await expect(call("results-select")).toHaveCount(1);
  await call("results-select").click();
  const writes: string[] = [];
  app.on("request", request => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${pathname}`);
  });
  await call("result-rerun").click();
  await expect(call("rerun-close")).toBeFocused();
  await capture(app, "product-rerun-ja-light-blue-1280");
  await app.keyboard.press("Escape");
  await expect(call("result-rerun")).toBeFocused();
  await call("results-tab-history").click();
  await call("results-select").first().click();
  await capture(app, "product-history-ja-light-blue-1280");
  await call("v1-board-delete-history").click();
  await expect(call("v1-delete-card")).toHaveAttribute("data-phase", "planned");
  await expect(call("v1-delete-cancel")).toBeFocused();
  await capture(app, "product-history-dialog-ja-light-blue-1280");
  await app.keyboard.press("Escape");
  await expect(call("v1-board-delete-history")).toBeFocused();
  expect(writes).toEqual([expect.stringMatching(/^POST \/api\/v1\/jobs\/[^/]+\/deletion-plans$/)]);
  await call("nav-queue").click();
  await app.route(/\/api\/v1\/(jobs|queue)(\?.*)?$/, route => route.abort("internetdisconnected"));
  await call("queue-reload").click();
  await expect(call("queue-error")).toHaveAttribute("data-error-kind", "network");
  await expect(call("queue-empty")).toHaveCount(0);
  await capture(app, "product-queue-error-ja-light-blue-1280");
  await noPageOverflow(app);
});
