import { GLOSSARY_ITEM_MAX, GLOSSARY_MAX } from "./settingsSchema";

/**
 * Glossary as text, one term per line (iteration 5, owner memo #36). Pure functions so the editor,
 * the TXT import mock and the tests share one set of rules — the SAME rules as the settings
 * validator (`validateSettings` / `glossaryEntryIssue`: at most 1000 terms, at most 256 characters
 * each, duplicates compared case-insensitively).
 *
 * What applying does, and nothing more:
 *  - spaces at the start / end of a line are removed;
 *  - empty lines are skipped;
 *  - a repeated term (same text ignoring case) is skipped; the first one is kept.
 * Order, case, full-width / half-width characters and every other character are kept as typed.
 * A line that is too long, or more than 1000 terms, blocks applying (nothing is cut off).
 */
export interface GlossaryAnalysis {
  /** Lines of the text (a trailing line break does not count as an extra line). */
  lineCount: number;
  /** Terms applying would store, in order. */
  terms: string[];
  emptyLines: number;
  /** 1-based line numbers of skipped duplicates. */
  duplicateLines: number[];
  /** Lines whose spaces at the ends are removed. */
  trimmedLines: number;
  /** 1-based line numbers longer than the limit (block applying). */
  tooLongLines: number[];
  /** More terms than allowed (blocks applying). */
  tooMany: boolean;
}

export function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split(/\r\n|\r|\n/);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

export function analyzeGlossary(text: string): GlossaryAnalysis {
  const lines = splitLines(text);
  const terms: string[] = [];
  const seen = new Set<string>();
  const out: GlossaryAnalysis = { lineCount: lines.length, terms, emptyLines: 0, duplicateLines: [], trimmedLines: 0, tooLongLines: [], tooMany: false };
  lines.forEach((raw, i) => {
    const term = raw.trim();
    if (term === "") {
      out.emptyLines += 1;
      return;
    }
    if (term !== raw) out.trimmedLines += 1;
    if (term.length > GLOSSARY_ITEM_MAX) {
      out.tooLongLines.push(i + 1);
      return;
    }
    const k = term.toLowerCase();
    if (seen.has(k)) {
      out.duplicateLines.push(i + 1);
      return;
    }
    seen.add(k);
    terms.push(term);
  });
  out.tooMany = terms.length > GLOSSARY_MAX;
  return out;
}

export function canApply(a: GlossaryAnalysis): boolean {
  return a.tooLongLines.length === 0 && !a.tooMany;
}

/** Text shown for a stored glossary: one term per line. */
export function glossaryToText(terms: readonly string[]): string {
  return terms.join("\n");
}

/** What a UTF-8 TXT file would contain on export (no BOM; the chosen line ending). */
export function glossaryToTxt(terms: readonly string[], newline: "lf" | "crlf"): string {
  const nl = newline === "crlf" ? "\r\n" : "\n";
  return terms.length === 0 ? "" : terms.join(nl) + nl;
}

/** Decodes a TXT file's (already UTF-8 decoded) content: a BOM is dropped; CRLF / LF / CR all end a line. */
export function decodeTxt(content: string): { text: string; bom: boolean; newline: "crlf" | "lf" | "mixed" | "none" } {
  const bom = content.startsWith("﻿");
  const body = bom ? content.slice(1) : content;
  const crlf = (body.match(/\r\n/g) ?? []).length;
  const lf = (body.match(/(?<!\r)\n/g) ?? []).length;
  const newline = crlf > 0 && lf > 0 ? "mixed" : crlf > 0 ? "crlf" : lf > 0 ? "lf" : "none";
  return { text: splitLines(body).join("\n"), bom, newline };
}

/** Import into a draft: replace it, or append below it (applying still removes duplicates). */
export function mergeImport(draft: string, imported: string, mode: "replace" | "append"): string {
  if (mode === "replace" || draft.trim() === "") return imported;
  return `${draft.replace(/[\r\n]+$/, "")}\n${imported}`;
}

// ---- synthetic TXT files for the import mock (nothing is read from the PC) ---------------------
export interface GlossarySample {
  name: string;
  /** File content as it would be decoded from UTF-8 (BOM and CRLF included where noted). */
  content: string;
}

function numbered(prefix: string, n: number): string[] {
  return Array.from({ length: n }, (_, i) => `${prefix}${String(i + 1).padStart(3, "0")}`);
}

export const GLOSSARY_SAMPLES: readonly GlossarySample[] = [
  // 120 terms, UTF-8 with BOM, Windows line endings: Japanese, English with capitals, full-width.
  { name: "glossary-sample-120.txt", content: "﻿" + [...numbered("議事録用語", 60), ...numbered("DemoTerm-", 50), ...numbered("ＡＢＣ製品", 10)].join("\r\n") + "\r\n" },
  // Problems on purpose: empty lines, spaces, a duplicate in other case, a 300-character line.
  { name: "glossary-with-issues.txt", content: ["議事録", "", "  Utteran  ", "utteran", "話者分離", "x".repeat(300), "最後の語"].join("\n") },
  // One more than the limit: applying is refused.
  { name: "glossary-sample-1001.txt", content: numbered("term-", 1001).join("\n") + "\n" },
];
