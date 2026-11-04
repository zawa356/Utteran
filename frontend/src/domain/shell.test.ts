import { describe, expect, it } from "vitest";
import { createFakeHost } from "../host/fake";
import { scriptedFetch } from "../test/fakeFetch";
import { createCore } from "./core";
import { SECTION_STATUS, SECTIONS } from "./shell";

describe("shell state", () => {
  it("mode switches keep navigation and domain state (state lives outside views)", () => {
    const core = createCore({ fetch: scriptedFetch([]), host: createFakeHost() });
    core.shell.navigate("settings");
    core.shell.showPane("queue");
    core.settings.store.patch({ draft: { theme: "dark", language: "en" } });
    core.shell.setMode("expert");
    core.shell.setMode("normal");
    expect(core.shell.store.get()).toMatchObject({ mode: "normal", section: "settings", expertPane: "queue" });
    expect(core.settings.store.get().draft).toEqual({ theme: "dark", language: "en" });
  });

  it("every section declares its migration status; only two are partial", () => {
    expect(Object.keys(SECTION_STATUS).sort()).toEqual([...SECTIONS].sort());
    expect(SECTIONS.filter((s) => SECTION_STATUS[s] === "partial").sort()).toEqual(["settings", "transcribe"]);
  });
});
