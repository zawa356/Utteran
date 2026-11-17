import { describe, expect, it } from "vitest";
import { createFakeHost } from "../host/fake";
import { scriptedFetch } from "../test/fakeFetch";
import { createCore } from "./core";
import { translate } from "../i18n";
import { previewNoticeKey, SECTION_STATUS, SECTIONS } from "./shell";

describe("shell state", () => {
  it("mode switches keep navigation and domain state (state lives outside views)", () => {
    const core = createCore({ fetch: scriptedFetch([]), host: createFakeHost() });
    core.shell.navigate("settings");
    core.shell.showPane("queue");
    const draft = { theme: "dark", language: "en", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "" } as const;
    core.settings.store.patch({ draft });
    core.shell.setMode("expert");
    core.shell.setMode("normal");
    expect(core.shell.store.get()).toMatchObject({ mode: "normal", section: "settings", expertPane: "queue" });
    expect(core.settings.store.get().draft).toEqual(draft);
  });

  it("every section declares its migration status; only two are partial", () => {
    expect(Object.keys(SECTION_STATUS).sort()).toEqual([...SECTIONS].sort());
    expect(SECTIONS.filter((s) => SECTION_STATUS[s] === "partial").sort()).toEqual(["settings", "transcribe"]);
  });

  it("the standing notice follows the server API mode: legacy text unchanged, v1 text only in v1-dev", () => {
    const fetch = scriptedFetch([]);
    const legacy = createCore({ fetch, host: createFakeHost() });
    const v1 = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
    expect(previewNoticeKey(legacy.apiMode)).toBe("app.previewNotice");
    expect(previewNoticeKey(v1.apiMode)).toBe("app.previewNoticeV1");
    // Anything but the exact server value is legacy (never inferred from elsewhere).
    expect(previewNoticeKey(createCore({ fetch, host: createFakeHost(), apiMode: "V1-DEV" as never }).apiMode)).toBe("app.previewNotice");
    // Legacy `--next-ui` keeps describing the legacy sequential submission.
    expect(translate("ja", "app.previewNotice")).toContain("複数ファイルの順次投入");
    expect(translate("en", "app.previewNotice")).toContain("sent one by one");
    // v1 texts (shell + Normal) no longer claim sequential / single-file-only / legacy history.
    for (const key of ["app.previewNoticeV1", "v1.mode.notice"] as const) {
      const ja = translate("ja", key);
      const en = translate("en", key);
      for (const wrong of ["順次投入", "単一ファイル", "旧画面"]) expect(ja).not.toContain(wrong);
      for (const wrong of ["one by one", "single-file", "legacy screen"]) expect(en).not.toContain(wrong);
      expect(ja).toContain("--next-ui-v1");
      expect(en).toContain("--next-ui-v1");
      expect(ja).toContain("Expert");
      expect(en).toContain("Expert");
      expect(ja).toContain("履歴");
      expect(en).toContain("history");
    }
    expect(translate("ja", "app.previewNoticeV1")).toContain("まだありません");
    expect(translate("en", "app.previewNoticeV1")).toContain("not here yet");
  });
});
