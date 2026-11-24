import { readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { choose, expect, SESSION_HEADER } from "./fixtures";
import { test } from "./isolatedV1";

async function createPreset(page: Page, name: string) {
  await expect(page.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  await page.getByTestId("v1-preset-create-open").click();
  await page.getByTestId("v1-preset-name").locator("input").fill(name);
  await page.getByTestId("v1-preset-check").click();
  await page.getByTestId("v1-preset-save").click();
  await expect(page.getByTestId("v1-preset-frozen")).toContainText(name);
}

test("Normal imports and exports TXT, confirms a revision, and uses it only after adoption", async ({ app, request, backend }) => {
  const policy = await request.post("/api/v1/execution-policies", {
    headers: { ...SESSION_HEADER, "Idempotency-Key": "ea-synthetic-policy-key" },
    data: {
      schema_version: 1, name: "EA CPU", mode: "auto", fixed_candidate_id: null,
      allowed_profile_ids: ["cpu"], objective: "balanced", max_memory_bytes: null,
      fallback: "none", execution_deadline_seconds: null,
    },
  });
  expect(policy.status()).toBe(201);
  await app.getByTestId("v1-lib-reload").click();
  await createPreset(app, "EA synthetic");
  await app.getByTestId("ea-manage").click();
  const editor = app.getByTestId("ea-manager");
  await expect(editor).toBeVisible();
  const textarea = editor.getByTestId("ea-glossary").locator("textarea").first();
  await textarea.fill("未適用");
  await textarea.dispatchEvent("compositionstart", { data: "未" });
  await textarea.press("Enter");
  await textarea.dispatchEvent("compositionend", { data: "未" });
  await expect(editor.getByTestId("ea-revise")).toBeDisabled();

  const file = editor.getByTestId("ea-glossary-import");
  await file.setInputFiles({ name: "invalid.txt", mimeType: "text/plain", buffer: Buffer.from([0xff]) });
  await expect(editor.getByTestId("ea-import-accept")).toHaveCount(0);
  await expect(textarea).toHaveValue(/^未適用/);
  const term = "x".repeat(215);
  const other = "y".repeat(215);
  await file.setInputFiles({ name: "terms.txt", mimeType: "text/plain", buffer: Buffer.from(`\uFEFF用語\r\n${term}\r${other}`) });
  await editor.getByTestId("ea-import-accept").click();
  await editor.getByTestId("ea-glossary-apply").click();
  const download = app.waitForEvent("download");
  await editor.getByTestId("ea-glossary-export").click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toBe("glossary.txt");
  expect(await readFile((await downloaded.path())!, "utf8")).toBe(`用語\n${term}\n${other}`);

  await editor.getByTestId("ea-revise").click();
  await editor.getByTestId("ea-confirm").click();
  await expect(editor.getByRole("status").filter({ hasText: "新revisionを保存しました" })).toBeVisible();
  await editor.getByRole("button", { name: "閉じる", exact: true }).click();
  const frozen = app.getByTestId("v1-preset-frozen");
  const newRevision = app.getByTestId("v1-preset-newer");
  await expect(newRevision).toBeVisible();
  await app.getByTestId("v1-preset-adopt").click();
  await expect(frozen).toContainText("保存済み用語集：3語");
  await choose(app, "v1-policy-select", /EA CPU/);

  await app.getByTestId("tx-input-path").locator("input").fill(join(backend.root, "in", "meeting.wav"));
  await app.getByTestId("tx-input-add-typed").click();
  await app.getByTestId("v1-output-path").locator("input").fill(join(backend.root, "out"));
  await app.getByTestId("v1-check").click();
  await expect(app.getByTestId("ea-validation-warnings")).toContainText("省かれる可能性");
  await expect(app.getByTestId("ea-validation-warnings")).toContainText("未検証");
  await expect(app.getByTestId("v1-resolution")).toBeVisible();
  await app.getByTestId("v1-submit").click();
  await expect(app.getByTestId("v1-accepted-note")).toBeVisible();
  const jobs = await request.get("/api/v1/jobs", { headers: SESSION_HEADER });
  expect((await jobs.json()).items[0].snapshot.preset_ref.revision).toBe(2);
  expect(await jobs.text()).not.toContain(term);

  await app.reload();
  await expect(app.getByTestId("v1-settings")).toHaveAttribute("data-lists", "done/done");
  await choose(app, "v1-preset-select", /EA synthetic/);
  await expect(frozen).toContainText("保存済み用語集：3語");
});

test("Expert reconciles a lost write response with GET and keeps the selected revision", async ({ app, backend }) => {
  await app.getByTestId("mode-expert").click();
  await createPreset(app, "EA original");
  let puts = 0;
  let reconciled = false;
  await app.route("**/api/v1/presets/pre_*", async route => {
    if (route.request().method() === "PUT") {
      puts += 1;
      expect((await route.fetch()).status()).toBe(200);
      await route.abort("timedout");
    } else {
      if (puts && !new URL(route.request().url()).search) reconciled = true;
      await route.continue();
    }
  });
  await app.getByTestId("ea-manage").click();
  const editor = app.getByTestId("ea-manager");
  await editor.getByTestId("ea-name").locator("input").fill("EA revised");
  await editor.getByTestId("ea-revise").click();
  await editor.getByTestId("ea-confirm").click();
  await expect(editor.getByRole("status").filter({ hasText: "同じ内容が保存されています" })).toBeVisible();
  expect(puts).toBe(1);
  expect(reconciled).toBe(true);
  await editor.getByRole("button", { name: "閉じる", exact: true }).click();
  await editor.getByTestId("ea-confirm").click();
  await expect(app.getByTestId("v1-preset-frozen")).toContainText("EA original");
  await expect(app.getByTestId("v1-preset-newer")).toBeVisible();
  await app.getByTestId("tx-input-path").locator("input").fill(join(backend.root, "in", "meeting.wav"));
  await app.getByTestId("tx-input-add-typed").click();
  await app.getByTestId("ea-row-glossary").click();
  const rowEditor = app.getByRole("dialog").filter({ has: app.getByTestId("ea-glossary") });
  await rowEditor.getByTestId("ea-glossary").locator("textarea").first().fill("行の用語");
  await rowEditor.getByTestId("ea-glossary-apply").click();
  await expect(rowEditor).toBeHidden();
  await app.getByTestId("ea-row-glossary").click();
  await expect(rowEditor.getByTestId("ea-glossary").locator("textarea").first()).toHaveValue("行の用語");
  await rowEditor.getByTestId("ea-row-glossary-inherit").click();
  await app.getByTestId("ea-row-glossary").click();
  await expect(rowEditor.getByTestId("ea-glossary").locator("textarea").first()).toHaveValue("");
});

test("CAS conflict shows the latest preset and keeps the English draft separate", async ({ app, request }) => {
  expect((await request.patch("/api/settings", { headers: SESSION_HEADER, data: { language: "en" } })).status()).toBe(200);
  await app.reload();
  await createPreset(app, "EA shared");
  const response = await request.get("/api/v1/presets", { headers: SESSION_HEADER });
  const saved = (await response.json()).items[0];
  await app.getByTestId("ea-manage").click();
  const editor = app.getByTestId("ea-manager");
  await editor.getByTestId("ea-name").locator("input").fill("EA local draft");
  const changed = await request.put(`/api/v1/presets/${saved.id}`, {
    headers: { ...SESSION_HEADER, "If-Match": '"1"' },
    data: { schema_version: 1, name: "EA other client", settings: saved.settings },
  });
  expect(changed.status()).toBe(200);
  await editor.getByTestId("ea-revise").click();
  await editor.getByTestId("ea-confirm").click();
  await expect(editor.getByText("EA other client", { exact: false })).toBeVisible();
  await expect(editor.getByTestId("ea-name").locator("input")).toHaveValue("EA local draft");
  await expect(editor.getByTestId("ea-revise")).toBeDisabled();
  await editor.getByRole("button", { name: "Close", exact: true }).click();
  await editor.getByTestId("ea-confirm").click();
  await expect(app.getByTestId("v1-preset-frozen")).toContainText("EA shared");
  await expect(app.getByTestId("v1-preset-newer")).toBeVisible();
});
