import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

/**
 * The production build (served at /next/ with --next-ui) must not contain the prototype.
 * Requires `npm run build` first (CI builds before the E2E steps); a missing build fails.
 */
const root = fileURLToPath(new URL("../../src/utteran_gui/web-next/", import.meta.url));
// Iteration 5 texts replaced some older markers (第4案, 承認して開始, デモ用の架空の例); the new ones are
// checked instead, plus markers of the iteration-5 additions (catalog fixture, sample folders, glossary samples).
const MARKERS = ["試作版（UI-3-0 第5案）", "インストール内容の確認", "utteran-preset", "blueLight", "架空の例です", "Utteran-demo", "glossary-sample-120", "kotoba-whisper-v2.0", "修復と初期化", "システムログを見る", "zawa356/Utteran/blob", "demo-model-balanced", "demo-profile-gpu", "greenLight", "src/preview", "utteran-ui3-preview", "sample-meeting-01"];

test("the production build has no prototype entry, module or text", () => {
  expect(existsSync(`${root}index.html`), "run `npm run build` before this check").toBe(true);
  const files = [`${root}index.html`, ...readdirSync(`${root}assets`).map((f) => `${root}assets/${f}`)];
  expect(readdirSync(root).filter((f) => f.endsWith(".html"))).toEqual(["index.html"]);
  for (const file of files) {
    const text = readFileSync(file, "utf-8");
    for (const marker of MARKERS) expect(text.includes(marker), `${marker} in ${file}`).toBe(false);
  }
});
