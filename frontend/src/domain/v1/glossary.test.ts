import { expect, it } from "vitest";
import { analyzeGlossary, glossaryTxt, MAX_GLOSSARY_BYTES, readGlossary } from "./glossary";

it("accepts BOM and mixed line endings and gives the original duplicate line", async () => {
  const file = new Blob(["\uFEFF 語句 \r\nUtteran\r\n\rutteran\nＡ\na"]);
  expect(analyzeGlossary(await readGlossary(file))).toMatchObject({
    valid: true,
    terms: ["語句", "Utteran", "Ａ", "a"],
    trimmed: [1],
    empty: [3],
    duplicates: [{ line: 4, first: 2 }],
  });
});

it("counts Unicode code points and rejects invalid, long or excess terms", () => {
  expect(analyzeGlossary("😀".repeat(256)).valid).toBe(true);
  expect(analyzeGlossary("😀".repeat(257))).toMatchObject({ valid: false, long: [1] });
  expect(analyzeGlossary("valid\ninvalid\0").valid).toBe(false);
  const thousand = Array.from({ length: 1000 }, (_, i) => `term${i}`).join("\n");
  expect(analyzeGlossary(thousand).valid).toBe(true);
  expect(analyzeGlossary(`${thousand}\nextra`)).toMatchObject({ valid: false, overflow: true });
});

it("rejects invalid UTF-8 and oversized files before producing text", async () => {
  await expect(readGlossary(new Blob([Uint8Array.of(0xef, 0xbb, 0xbf, 0xff)]))).rejects.toThrow();
  await expect(readGlossary(new Blob([new Uint8Array(MAX_GLOSSARY_BYTES + 1)]))).rejects.toThrow(RangeError);
});

it.each(["LF", "CRLF"] as const)("round trips BOM-free UTF-8 with %s", async (ending) => {
  const terms = ["音声", "😀"];
  const file = glossaryTxt(terms, ending);
  expect(await file.text()).toBe(terms.join(ending === "LF" ? "\n" : "\r\n"));
  expect(new Uint8Array(await file.arrayBuffer())[0]).toBe(0xe9);
  expect(analyzeGlossary(await readGlossary(file)).terms).toEqual(terms);
});
