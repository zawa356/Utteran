import { describe, expect, it } from "vitest";
import { en } from "./en";
import { translatePreview } from "./index";
import { ja } from "./ja";

describe("prototype i18n", () => {
  it("ja and en have the same keys and no empty strings", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ja).sort());
    for (const value of [...Object.values(ja), ...Object.values(en)]) expect(value.trim()).not.toBe("");
  });

  it("states 'prototype / synthetic / nothing is processed' in one short line in both languages", () => {
    const ja1 = `${translatePreview("ja", "banner.title")} ${translatePreview("ja", "banner.body")}`;
    const en1 = `${translatePreview("en", "banner.title")} ${translatePreview("en", "banner.body")}`;
    expect(ja1).toMatch(/試作.*合成データ.*行いません/);
    expect(en1).toMatch(/prototype.*synthetic.*nothing is/i);
    expect(ja1.length).toBeLessThanOrEqual(60);
    expect(en1.length).toBeLessThanOrEqual(110);
  });

  it("product texts carry no connection jargon (owner memo #11)", () => {
    const product = Object.entries(ja).filter(([k]) => !k.startsWith("toolbar.") && !k.startsWith("syncOption."));
    for (const [key, value] of product) expect(value, key).not.toMatch(/同期|再同期|epoch|\/sync|SSE|接続:/);
  });

  it("uses the same placeholders in both languages", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const key of Object.keys(ja) as (keyof typeof ja)[]) expect(names(en[key]), key).toEqual(names(ja[key]));
  });
});
