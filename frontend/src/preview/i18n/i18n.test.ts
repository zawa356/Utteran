import { describe, expect, it } from "vitest";
import { en } from "./en";
import { translatePreview } from "./index";
import { ja } from "./ja";

describe("prototype i18n", () => {
  it("ja and en have the same keys and no empty strings", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ja).sort());
    for (const value of [...Object.values(ja), ...Object.values(en)]) expect(value.trim()).not.toBe("");
  });

  it("states 'prototype / synthetic / nothing is processed' in both languages", () => {
    expect(translatePreview("ja", "banner.title")).toBe("プロトタイプ／合成データ／処理しません");
    expect(translatePreview("en", "banner.title")).toMatch(/prototype.*synthetic.*nothing is processed/i);
  });

  it("uses the same placeholders in both languages", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const key of Object.keys(ja) as (keyof typeof ja)[]) expect(names(en[key]), key).toEqual(names(ja[key]));
  });
});
