import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import { en } from "./en";
import { errorMessage, translate } from "./index";
import { ja } from "./ja";

describe("i18n", () => {
  it("ja and en have the same keys and no empty strings", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ja).sort());
    for (const value of [...Object.values(ja), ...Object.values(en)]) expect(value.trim()).not.toBe("");
  });

  it("interpolates parameters and leaves unknown ones visible", () => {
    expect(translate("en", "env.active", { profile: "cpu" })).toBe("Profile used by the server: cpu");
    expect(translate("ja", "env.active")).toContain("{profile}");
  });

  it("maps known codes, falls back by kind for unknown codes, and flags ambiguous outcomes", () => {
    expect(errorMessage("en", new ApiError({ kind: "conflict", code: "execution_unavailable" }))).toContain("unverified");
    expect(errorMessage("ja", new ApiError({ kind: "conflict", code: "some_future_code" }))).toBe(ja["error.conflict"]);
    expect(errorMessage("en", new ApiError({ kind: "timeout", outcomeUnknown: true }))).toBe(en["error.outcomeUnknown"]);
  });
});
