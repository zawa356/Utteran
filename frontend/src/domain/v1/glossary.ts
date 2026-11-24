export const MAX_GLOSSARY_BYTES = 1024 * 1024;
const MAX_TERMS = 1000;
const MAX_TERM_LENGTH = 256;
const INVALID_CHARACTERS = /[\p{Cc}\p{Cs}\uFEFF]/u;

export function analyzeGlossary(text: string) {
  const terms: string[] = [];
  const empty: number[] = [];
  const trimmed: number[] = [];
  const duplicates: { line: number; first: number }[] = [];
  const invalid: number[] = [];
  const long: number[] = [];
  const lines = text === "" ? [] : text.split(/\r\n|\n|\r/);
  const seen = new Map<string, number>();
  for (const [index, raw] of lines.entries()) {
    const line = index + 1;
    const term = raw.trim();
    if (INVALID_CHARACTERS.test(raw.replace(/\t/g, ""))) invalid.push(line);
    if (term !== raw) trimmed.push(line);
    if (!term) {
      empty.push(line);
      continue;
    }
    if ([...term].length > MAX_TERM_LENGTH) long.push(line);
    const key = term.toLowerCase();
    const first = seen.get(key);
    if (first !== undefined) {
      duplicates.push({ line, first });
      continue;
    }
    seen.set(key, line);
    terms.push(term);
  }
  const overflow = terms.length > MAX_TERMS;
  return { terms, empty, trimmed, duplicates, invalid, long, overflow, valid: !overflow && !invalid.length && !long.length };
}

export async function readGlossary(file: Pick<File, "size" | "arrayBuffer">): Promise<string> {
  if (file.size > MAX_GLOSSARY_BYTES) throw new RangeError("size");
  return new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
}

export function glossaryTxt(terms: readonly string[], newline: "LF" | "CRLF"): Blob {
  return new Blob([terms.join(newline === "LF" ? "\n" : "\r\n")], { type: "text/plain;charset=utf-8" });
}
