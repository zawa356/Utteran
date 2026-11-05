import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

/**
 * The production build (served at /next/ with --next-ui) must not contain the prototype.
 * Requires `npm run build` first (CI builds before the E2E steps); a missing build fails.
 */
const root = fileURLToPath(new URL("../../src/utteran_gui/web-next/", import.meta.url));
const MARKERS = ["プロトタイプ／合成データ／処理しません", "demo-model-balanced", "src/preview", "utteran-ui3-preview", "sample-meeting-01"];

test("the production build has no prototype entry, module or text", () => {
  expect(existsSync(`${root}index.html`), "run `npm run build` before this check").toBe(true);
  const files = [`${root}index.html`, ...readdirSync(`${root}assets`).map((f) => `${root}assets/${f}`)];
  expect(readdirSync(root).filter((f) => f.endsWith(".html"))).toEqual(["index.html"]);
  for (const file of files) {
    const text = readFileSync(file, "utf-8");
    for (const marker of MARKERS) expect(text.includes(marker), `${marker} in ${file}`).toBe(false);
  }
});
