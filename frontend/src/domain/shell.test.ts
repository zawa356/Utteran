import { describe, expect, it } from "vitest";
import { createFakeHost } from "../host/fake";
import { scriptedFetch } from "../test/fakeFetch";
import { createCore } from "./core";
import { translate } from "../i18n";
import { EXPERT_SECTIONS, isRail, NORMAL_SECTIONS, previewNoticeKey, SECTION_STATUS, SECTIONS, sectionStatus, sectionsFor, ShellController } from "./shell";

describe("shell state", () => {
  it("mode switches keep navigation and domain state (state lives outside views)", () => {
    const core = createCore({ fetch: scriptedFetch([]), host: createFakeHost() });
    core.shell.navigate("settings");
    core.shell.showPane("queue");
    const draft = { theme: "dark", language: "en", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "" } as const;
    core.settings.store.patch({ draft });
    core.shell.setMode("expert");
    core.shell.setMode("normal");
    // D1: each mode keeps its own last page; Normal is back on its settings page.
    expect(core.shell.store.get()).toMatchObject({ mode: "normal", section: "settings", expertPane: "queue" });
    expect(core.settings.store.get().draft).toEqual(draft);
  });

  // Bundle D1 (Design Freeze): Normal = 3 app-bar destinations, Expert = 6 sidebar destinations.
  it("D1-01: Normal offers transcribe/setup/settings only; Expert offers all six, in the Design Freeze order", () => {
    expect([...NORMAL_SECTIONS]).toEqual(["transcribe", "setup", "settings"]);
    expect([...EXPERT_SECTIONS]).toEqual(["transcribe", "queue", "results", "models", "setup", "settings"]);
    expect(sectionsFor("normal")).toEqual(NORMAL_SECTIONS);
    expect(sectionsFor("expert")).toEqual(EXPERT_SECTIONS);
    expect([...SECTIONS].sort()).toEqual([...EXPERT_SECTIONS].sort());
  });

  it("D1-02: each mode returns to its own last page (Normal settings ⇄ Expert queue)", () => {
    const shell = new ShellController();
    expect(shell.navigate("settings")).toBe(true);
    shell.setMode("expert");
    // First visit of Expert: its own default (transcription), not Normal's page.
    expect(shell.store.get().section).toBe("transcribe");
    expect(shell.navigate("queue")).toBe(true);
    shell.setMode("normal");
    expect(shell.store.get()).toMatchObject({ mode: "normal", section: "settings" });
    shell.setMode("expert");
    expect(shell.store.get()).toMatchObject({ mode: "expert", section: "queue" });
  });

  it("D1-02: an Expert-only page is never shown in Normal; Normal falls back to its last page or transcription", () => {
    const shell = new ShellController();
    // Normal cannot navigate to an Expert-only page: refused, nothing changes.
    for (const id of ["queue", "results", "models"] as const) {
      expect(shell.navigate(id)).toBe(false);
      expect(shell.store.get()).toMatchObject({ mode: "normal", section: "transcribe", normalSection: "transcribe" });
    }
    shell.setMode("expert");
    shell.navigate("results");
    shell.setMode("normal");
    expect(shell.store.get().section).toBe("transcribe");
    // A page that both modes offer is remembered per mode.
    shell.navigate("setup");
    shell.setMode("expert");
    expect(shell.store.get().section).toBe("results");
    shell.navigate("settings");
    shell.setMode("normal");
    expect(shell.store.get().section).toBe("setup");
  });

  it("D1-02: the shown page always belongs to the current mode, whatever the sequence", () => {
    const shell = new ShellController();
    const steps: (() => void)[] = [
      () => shell.setMode("expert"),
      () => shell.setMode("normal"),
      ...EXPERT_SECTIONS.map((id) => () => void shell.navigate(id)),
      () => shell.toggleNav(false),
      () => shell.toggleNav(true),
      () => shell.openExpertTranscribe(),
    ];
    let seed = 7;
    for (let i = 0; i < 300; i += 1) {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      steps[seed % steps.length]!();
      const s = shell.store.get();
      expect(sectionsFor(s.mode)).toContain(s.section);
      expect(s.section).toBe(s.mode === "normal" ? s.normalSection : s.expertSection);
    }
  });

  it("D1-02: 'several files (Expert)' opens the Expert transcription page, even if Expert was elsewhere", () => {
    const shell = new ShellController();
    shell.setMode("expert");
    shell.navigate("models");
    shell.setMode("normal");
    shell.openExpertTranscribe();
    expect(shell.store.get()).toMatchObject({ mode: "expert", section: "transcribe" });
  });

  it("D1-07: the sidebar toggle works at every width; wide and narrow choices are kept apart", () => {
    const shell = new ShellController();
    const s = () => shell.store.get();
    // Wide windows start expanded, narrow windows start as a rail.
    expect(isRail(s(), false)).toBe(false);
    expect(isRail(s(), true)).toBe(true);
    shell.toggleNav(true); // narrow: expand (was disabled before D1)
    expect(isRail(s(), true)).toBe(false);
    expect(isRail(s(), false)).toBe(false);
    shell.toggleNav(false); // wide: collapse
    expect(isRail(s(), false)).toBe(true);
    expect(isRail(s(), true)).toBe(false);
    shell.toggleNav(true);
    expect(isRail(s(), true)).toBe(true);
    // Mode switches never touch the sidebar choice.
    shell.setMode("expert");
    shell.setMode("normal");
    expect(s()).toMatchObject({ navCollapsed: true, navExpandedNarrow: false });
  });

  it("D1-03: navigation and mode switches send no request and keep inputs, selection, overrides and drafts", () => {
    const fetch = scriptedFetch([]); // any request would throw "unexpected request"
    const core = createCore({ fetch, host: createFakeHost() });
    const draft = { theme: "dark", language: "en", defaultProfile: null, defaultInputDir: "", defaultOutputDir: "" } as const;
    core.settings.store.patch({ draft });
    const inputs = core.inputs.store.get();
    const form = core.transcription.store.get();
    const job = core.job.store.get();
    core.shell.navigate("settings");
    core.shell.setMode("expert");
    for (const id of EXPERT_SECTIONS) core.shell.navigate(id);
    core.shell.showPane("queue");
    core.shell.toggleNav(false);
    core.shell.toggleNav(true);
    core.shell.setMode("normal");
    for (const id of NORMAL_SECTIONS) core.shell.navigate(id);
    core.shell.openExpertTranscribe();
    expect(fetch.calls).toEqual([]);
    expect(core.settings.store.get().draft).toEqual(draft);
    // Same objects: nothing was rebuilt, reset or copied by the shell.
    expect(core.inputs.store.get()).toBe(inputs);
    expect(core.transcription.store.get()).toBe(form);
    expect(core.job.store.get()).toBe(job);
    expect(core.shell.store.get().expertPane).toBe("queue");
  });

  it("D1-04: in v1-dev the shell reuses the one set of v1 controllers (no second controller, no request)", () => {
    const fetch = scriptedFetch([]);
    const core = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
    const v1 = core.v1!;
    const stores = [v1.library.store.get(), v1.job.store.get(), v1.batch.store.get(), v1.board.store.get(), v1.deletion.store.get()];
    core.shell.setMode("expert");
    for (const id of EXPERT_SECTIONS) core.shell.navigate(id);
    core.shell.setMode("normal");
    core.shell.setMode("expert");
    expect(core.v1).toBe(v1);
    expect([v1.library.store.get(), v1.job.store.get(), v1.batch.store.get(), v1.board.store.get(), v1.deletion.store.get()]).toEqual(stores);
    expect(fetch.calls).toEqual([]);
  });

  it("every section declares its migration status; only two are partial", () => {
    expect(Object.keys(SECTION_STATUS).sort()).toEqual([...SECTIONS].sort());
    expect(SECTIONS.filter((s) => SECTION_STATUS[s] === "partial").sort()).toEqual(["settings", "transcribe"]);
  });

  it("D2A-01: the queue page is partial only in v1-dev; legacy (`--next-ui`) keeps it not migrated", () => {
    expect(sectionStatus("queue", "v1-dev")).toBe("partial");
    expect(sectionStatus("queue", "legacy")).toBe("not_migrated");
    // The other sections retain their migration status.
    for (const mode of ["legacy", "v1-dev"] as const) {
      expect(SECTIONS.filter((s) => s !== "queue" && s !== "results").map((s) => sectionStatus(s, mode))).toEqual(SECTIONS.filter((s) => s !== "queue" && s !== "results").map((s) => SECTION_STATUS[s]));
    }
    // The static table (used for legacy) is not rewritten for every mode.
    expect(SECTION_STATUS.queue).toBe("not_migrated");
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
    expect(translate("ja", "app.previewNoticeV1")).toContain("結果と履歴");
    expect(translate("en", "app.previewNoticeV1")).toContain("results and history");
  });
});

it("D3A-01: only explicit v1 opts in to the Expert results page", () => {
  expect(sectionStatus("results", "v1-dev")).toBe("partial");
  expect(sectionStatus("results", "legacy")).toBe("not_migrated");
});
