import { describe, expect, it } from "vitest";
import { analyzeGlossary, canApply, decodeTxt, GLOSSARY_SAMPLES, glossaryToTxt, mergeImport, splitLines } from "./glossaryText";
import { DEFAULT_COMMON, GLOSSARY_MAX, validateSettings } from "./settingsSchema";

describe("glossary text (iteration 5, #36)", () => {
  it("keeps order, case and Unicode; skips empty lines and later duplicates; trims only line ends", () => {
    const a = analyzeGlossary("議事録\n\n  Utteran  \nutteran\nＡＢＣ\n話者 分離\n");
    expect(a.terms).toEqual(["議事録", "Utteran", "ＡＢＣ", "話者 分離"]);
    expect(a).toMatchObject({ lineCount: 6, emptyLines: 1, duplicateLines: [4], trimmedLines: 1, tooLongLines: [], tooMany: false });
    expect(canApply(a)).toBe(true);
  });

  it("agrees with the settings validator: what it applies is valid; too long / too many are refused, not cut", () => {
    const ok = analyzeGlossary(Array.from({ length: GLOSSARY_MAX }, (_, i) => `語${i}`).join("\n"));
    expect(ok.terms).toHaveLength(GLOSSARY_MAX);
    expect(canApply(ok)).toBe(true);
    expect(validateSettings({ ...DEFAULT_COMMON, glossary: ok.terms })).toEqual([]);
    const over = analyzeGlossary(Array.from({ length: GLOSSARY_MAX + 1 }, (_, i) => `語${i}`).join("\n"));
    expect(over.tooMany).toBe(true);
    expect(canApply(over)).toBe(false);
    const long = analyzeGlossary(`a\n${"x".repeat(257)}\nb`);
    expect(long.tooLongLines).toEqual([2]);
    expect(canApply(long)).toBe(false);
    expect(analyzeGlossary("x".repeat(256)).tooLongLines).toEqual([]);
  });

  it("TXT import: BOM dropped, CRLF / LF / CR lines; replace or append; export with the chosen line ending", () => {
    const sample = GLOSSARY_SAMPLES[0]!;
    const d = decodeTxt(sample.content);
    expect(d).toMatchObject({ bom: true, newline: "crlf" });
    expect(analyzeGlossary(d.text).terms).toHaveLength(120);
    expect(analyzeGlossary(d.text).terms[0]).toBe("議事録用語001"); // no BOM in the first term
    expect(decodeTxt("a\r\nb\nc").newline).toBe("mixed");
    expect(splitLines("a\rb")).toEqual(["a", "b"]);
    expect(mergeImport("x", "y", "replace")).toBe("y");
    expect(mergeImport("x\n", "y", "append")).toBe("x\ny");
    expect(glossaryToTxt(["a", "b"], "crlf")).toBe("a\r\nb\r\n");
    expect(canApply(analyzeGlossary(decodeTxt(GLOSSARY_SAMPLES[2]!.content).text))).toBe(false); // 1001 terms
  });
});
