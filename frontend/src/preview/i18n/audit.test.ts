import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { en } from "./en";
import { ja } from "./ja";
import { referencedKeys } from "./keyUsage";

/**
 * Text audit (iteration 5, owner memo #29). Enumerates every prototype text key and checks that it
 * is used somewhere (no stale texts are kept), and guards the wording rules the audit decided:
 * no repeated "(who spoke when)"-style explanations in labels, no "(value not shown)" asides,
 * no "steps to run" prose. The screen-by-screen review is recorded in docs/architecture/ui3/iteration-5.md.
 */
const ROOT = join(__dirname, "..");
function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "i18n") out.push(...sources(p));
    } else if (/\.(vue|ts)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(readFileSync(p, "utf8"));
  }
  return out;
}

describe("prototype text audit (#29)", () => {
  const keys = Object.keys(ja);
  const { used } = referencedKeys(keys, sources(ROOT));

  it("every text key is used by the prototype (no stale texts)", () => {
    expect(keys.filter((k) => !used.has(k))).toEqual([]);
  });

  it("labels do not repeat explanations in brackets; no '(value not shown)' or 'steps to run' prose", () => {
    for (const [k, v] of Object.entries(ja)) {
      expect(v, k).not.toMatch(/話者分離（誰が|値は表示しません|実行すること|判定の根拠/);
    }
    for (const [k, v] of Object.entries(en)) expect(v, k).not.toMatch(/who spoke when\)|value not shown/i);
  });

  it("decision- and safety-relevant texts are kept (terms, token, nothing deleted, prototype limits)", () => {
    expect(ja["wizard.cred.agreement"]).toMatch(/利用条件/);
    expect(ja["wizard.cred.absent"]).toMatch(/トークン/);
    expect(ja["history.deleteKeepsFiles"]).toMatch(/出力ファイルは削除しません/);
    expect(ja["maint.reset.later"]).toMatch(/何も消していません/);
    expect(ja["output.pickNote"]).toMatch(/表示・作成しません/);
    expect(ja["glossary.importNote"]).toMatch(/読み込みません/);
  });
});
