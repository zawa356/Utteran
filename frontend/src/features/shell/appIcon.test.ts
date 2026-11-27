import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/** The v1 app bar shows the EXISTING Utteran icon (#96, Design Freeze brand): the copy must equal icon/utteran.svg. */
describe("product app icon", () => {
  it("is the repository icon, unchanged", () => {
    const original = readFileSync(fileURLToPath(new URL("../../../../icon/utteran.svg", import.meta.url)));
    const copy = readFileSync(fileURLToPath(new URL("../../assets/utteran.svg", import.meta.url)));
    expect(copy.equals(original)).toBe(true);
  });
});
