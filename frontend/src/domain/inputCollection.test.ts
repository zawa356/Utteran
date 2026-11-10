import { describe, expect, it } from "vitest";
import { HostUnavailableError } from "../host";
import { createFakeHost } from "../host/fake";
import { b1Harness, envState, media, MEDIA, OUT, settingsState, twoBackends } from "../test/admissionHarness";
import { environment } from "../test/legacyFixtures";
import { applyFormatChanges, effectiveDraft, formatState, MAX_INPUTS, summarize } from "./inputCollection";

const rows = (h: ReturnType<typeof b1Harness>) => h.inputs.store.get().rows;
const notice = (h: ReturnType<typeof b1Harness>) => h.inputs.store.get().notice;

describe("adding inputs (picker, drop, typed)", () => {
  it("the picker adds one row per call (never replaces); cancel and a missing host change nothing", async () => {
    const h = b1Harness();
    const host = createFakeHost();
    host.nextPick = media("one.wav");
    await h.inputs.pick(host);
    host.nextPick = media("two.wav");
    await h.inputs.pick(host);
    expect(rows(h).map((r) => [r.inputPath, r.source])).toEqual([
      [media("one.wav"), "picker"],
      [media("two.wav"), "picker"],
    ]);
    expect(h.inputs.primary()?.inputPath).toBe(media("two.wav")); // the newest is Normal's current file
    host.nextPick = null;
    await h.inputs.pick(host);
    expect(rows(h)).toHaveLength(2);
    await h.inputs.pick({ ...host, pickPath: () => Promise.reject(new HostUnavailableError()) });
    expect(h.inputs.store.get().pickError).toBe("host_unavailable");
    expect(rows(h)).toHaveLength(2);
    expect(host.picks).toEqual(["input_file", "input_file", "input_file"]);
  });

  it("a native drop adds several files at once; folders/unknown are refused and counted, never taken as files", () => {
    const h = b1Harness();
    h.inputs.acceptDrop([
      { path: media("a.wav"), kind: "file" },
      { path: media("b.wav"), kind: "file" },
      { path: media("c.wav"), kind: "file" },
    ]);
    expect(rows(h)).toHaveLength(3);
    expect(notice(h)).toEqual({ kind: "added", count: 3 });
    expect(h.inputs.primary()?.inputPath).toBe(media("a.wav"));
    h.inputs.acceptDrop([
      { path: media("d.wav"), kind: "file" },
      { path: MEDIA, kind: "folder" },
      { path: media("mystery"), kind: "unknown" },
      { path: media("a.wav"), kind: "file" }, // duplicate
      { path: "relative/e.wav", kind: "file" }, // unusable path
    ]);
    expect(rows(h).map((r) => r.inputPath)).toEqual([media("a.wav"), media("b.wav"), media("c.wav"), media("d.wav")]);
    expect(notice(h)).toEqual({ kind: "drop", added: 1, duplicates: 1, folders: 1, unknown: 1, invalid: 1 });
    expect(rows(h).every((r) => r.source === "drop")).toBe(true);
    const before = h.inputs.store.get();
    h.inputs.acceptDrop([]);
    expect(h.inputs.store.get()).toBe(before);
  });

  it("typed paths are added by an explicit Add; relative/control/whitespace are refused with a reason; duplicates fold Windows spelling", () => {
    const h = b1Harness();
    for (const [value, problem] of [
      ["relative/x.wav", "relative"],
      [`${media("x.wav")} `, "whitespace"],
      [`${media("x")}\u0007.wav`, "control"],
      [`${MEDIA}/`, "folder_like"],
      ["", "empty"],
    ] as const) {
      h.inputs.setTyped(value);
      expect(h.inputs.addTyped()).toBe(false);
      expect(notice(h)).toEqual({ kind: "invalid", problem });
      expect(h.inputs.store.get().typed).toBe(value); // kept for correction, never trimmed
    }
    h.inputs.setTyped("C:\\Media\\Talk.WAV");
    expect(h.inputs.addTyped()).toBe(true);
    expect(h.inputs.store.get().typed).toBe("");
    h.inputs.setTyped("c:/media/talk.wav");
    expect(h.inputs.addTyped()).toBe(false);
    expect(notice(h)).toEqual({ kind: "duplicate" });
    expect(rows(h)).toHaveLength(1);
    expect(rows(h)[0]!.inputPath).toBe("C:\\Media\\Talk.WAV"); // kept exactly as given
  });

  it("the fake host delivers drops to a subscriber until it unsubscribes (moved from the S3-A form test)", () => {
    const h = b1Harness();
    const host = createFakeHost();
    const unsubscribe = host.onDroppedPaths((paths) => h.inputs.acceptDrop(paths));
    host.drop([{ path: media("a.wav"), kind: "file" }]);
    expect(rows(h)).toHaveLength(1);
    unsubscribe();
    host.drop([{ path: media("b.wav"), kind: "file" }]);
    expect(rows(h)).toHaveLength(1);
  });

  it(`at most ${MAX_INPUTS} inputs: an operation that would exceed it adds nothing (no silent truncation)`, () => {
    const h = b1Harness();
    h.inputs.acceptDrop(Array.from({ length: MAX_INPUTS - 2 }, (_, i) => ({ path: media(`f${i}.wav`), kind: "file" as const })));
    expect(rows(h)).toHaveLength(MAX_INPUTS - 2);
    h.inputs.acceptDrop([0, 1, 2].map((i) => ({ path: media(`extra${i}.wav`), kind: "file" as const })));
    expect(rows(h)).toHaveLength(MAX_INPUTS - 2);
    expect(notice(h)).toEqual({ kind: "limit", max: MAX_INPUTS, requested: 3, room: 2 });
    h.inputs.acceptDrop([0, 1].map((i) => ({ path: media(`extra${i}.wav`), kind: "file" as const })));
    expect(rows(h)).toHaveLength(MAX_INPUTS);
    h.inputs.setTyped(media("one-more.wav"));
    expect(h.inputs.addTyped()).toBe(false);
    expect(notice(h)).toMatchObject({ kind: "limit", room: 0 });
  });
});

describe("ids, selection and removal", () => {
  it("row ids are stable, unique and not the path; selection is shared and kept", () => {
    const h = b1Harness();
    h.inputs.acceptDrop([
      { path: media("a.wav"), kind: "file" },
      { path: media("b.wav"), kind: "file" },
    ]);
    const ids = rows(h).map((r) => r.clientFileId);
    expect(new Set(ids).size).toBe(2);
    expect(ids.some((id) => id.includes("synthetic") || id.includes("wav"))).toBe(false);
    h.inputs.toggle(ids[0]!, false);
    h.inputs.setPrimary(ids[1]!);
    expect(rows(h).map((r) => r.clientFileId)).toEqual(ids);
    expect(h.inputs.selected().map((r) => r.clientFileId)).toEqual([ids[1]]);
    h.inputs.selectAll();
    expect(h.inputs.selected()).toHaveLength(2);
    h.inputs.clearSelection();
    expect(h.inputs.selected()).toHaveLength(0);
  });

  it("only rows that were never sent (or were refused) can be removed; accepted/unknown stay and stay guarded", async () => {
    const h = b1Harness();
    h.form.choose("asrModel", "synthetic-a");
    h.inputs.acceptDrop(["a.wav", "b.wav", "c.wav", "d.wav"].map((n) => ({ path: media(n), kind: "file" as const })));
    const [a, b, c] = rows(h);
    const send = (row: typeof a, answer: (i: number) => void, index: number) => {
      const sent = h.gate.admit(h.inputs.evaluateRow(row!).request!, { clientFileId: row!.clientFileId, origin: "single" });
      answer(index);
      return sent;
    };
    await send(a, h.jobs.acceptAt, 0);
    await send(b, h.jobs.loseAt, 1);
    await send(c, (i) => h.jobs.refuseAt(i), 2);
    expect(rows(h).map((r) => h.inputs.statusOf(r))).toEqual(["accepted", "unknown", "rejected", "not_submitted"]);
    h.inputs.selectAll();
    h.inputs.removeSelected();
    expect(rows(h).map((r) => r.inputPath)).toEqual([media("a.wav"), media("b.wav")]);
    expect(notice(h)).toEqual({ kind: "removed", removed: 2, kept: 2 });
    // Hiding is impossible for guarded rows, and re-adding the same path is a duplicate.
    h.inputs.setTyped(media("a.wav"));
    expect(h.inputs.addTyped()).toBe(false);
    expect(h.inputs.evaluateRow(rows(h)[0]!)).toMatchObject({ sameInput: true, request: null });
    expect(h.inputs.evaluateRow(rows(h)[1]!)).toMatchObject({ sameInput: true, request: null });
  });

  it("a refused row becomes 'not sent' again only by an explicit reset", async () => {
    const h = b1Harness();
    h.form.choose("asrModel", "synthetic-a");
    h.inputs.setTyped(media("r.wav"));
    h.inputs.addTyped();
    const row = rows(h)[0]!;
    const sent = h.gate.admit(h.inputs.evaluateRow(row).request!, { clientFileId: row.clientFileId, origin: "single" });
    h.jobs.refuseAt(0);
    await sent;
    expect(h.inputs.statusOf(rows(h)[0]!)).toBe("rejected");
    h.inputs.resetRefusal(row.clientFileId);
    expect(h.inputs.statusOf(rows(h)[0]!)).toBe("not_submitted");
    expect(h.gate.store.get().records).toHaveLength(1); // the ledger keeps the refusal
  });
});

describe("common settings + per-row overrides", () => {
  function three(env = twoBackends()) {
    const h = b1Harness(env);
    h.form.choose("asrBackend", "faster-whisper"); // two backends: nothing is filled automatically
    h.form.choose("asrModel", "synthetic-a");
    h.inputs.acceptDrop(["a.wav", "b.wav", "c.wav"].map((n) => ({ path: media(n), kind: "file" as const })));
    const ids = rows(h).map((r) => r.clientFileId);
    const only = (...keep: string[]) => {
      h.inputs.clearSelection();
      for (const id of keep) h.inputs.toggle(id, true);
    };
    const eff = (i: number) => effectiveDraft(rows(h)[i]!, h.form.store.get().draft);
    return { h, ids, only, eff };
  }

  it("inherit → override → follow the common value again; common changes reach only inheriting rows", () => {
    const { h, ids, only, eff } = three();
    only(ids[1]!);
    h.inputs.stage({ language: "en" });
    expect(eff(1).language).toBe("ja"); // nothing changes before Apply
    expect(h.inputs.applyStaged()).toBe(1);
    expect(rows(h)[1]!.overrides).toEqual({ language: "en" });
    h.form.edit({ language: "auto" });
    expect([eff(0).language, eff(1).language, eff(2).language]).toEqual(["auto", "en", "auto"]);
    h.inputs.resetToCommon(["language"]);
    expect(rows(h)[1]!.overrides).toEqual({});
    expect(eff(1).language).toBe("auto");
  });

  it("a backend override drops the old model/device, fills single choices and never inherits the other backend's model", () => {
    const { h, ids, only, eff } = three();
    only(ids[0]!);
    h.inputs.stage({ asrModel: "synthetic-b" });
    h.inputs.applyStaged();
    h.inputs.stage({ asrBackend: "synthetic-cpp" });
    h.inputs.applyStaged();
    expect(rows(h)[0]!.overrides).toEqual({ asrBackend: "synthetic-cpp", asrModel: "cpp-only", asrDevice: "cpu" });
    expect(h.inputs.evaluateRow(rows(h)[0]!).request).toMatchObject({ asrBackend: "synthetic-cpp", asrModel: "cpp-only", asrDevice: "cpu" });
    // Common model changes do not leak into the detached row.
    h.form.choose("asrModel", "synthetic-b");
    expect(eff(0).asrModel).toBe("cpp-only");
    expect(eff(1).asrModel).toBe("synthetic-b");
    // Without a model override, a different backend has no model (explicit error, not a guess).
    h.inputs.resetToCommon(["asrModel"]);
    expect(eff(0)).toMatchObject({ asrBackend: "synthetic-cpp", asrModel: null });
    expect(h.inputs.evaluateRow(rows(h)[0]!).issues).toContainEqual({ field: "asrModel", problem: "missing" });
    h.inputs.resetToCommon(["asrBackend"]);
    expect(rows(h)[0]!.overrides).toEqual({});
  });

  it("an override the current environment no longer offers is reported, never replaced", () => {
    const { h, ids, only } = three();
    only(ids[2]!);
    h.inputs.stage({ asrBackend: "synthetic-cpp" });
    h.inputs.applyStaged();
    h.env.set(envState(environment("cuda"))); // the second backend is gone
    expect(rows(h)[2]!.overrides.asrBackend).toBe("synthetic-cpp");
    const evaluation = h.inputs.evaluateRow(rows(h)[2]!);
    expect(evaluation.request).toBeNull();
    expect(evaluation.issues).toContainEqual({ field: "asrBackend", problem: "unavailable" });
    // No environment answer at all: nothing can be sent.
    h.env.set(envState(null));
    expect(h.inputs.evaluateRow(rows(h)[0]!).issues).toContainEqual({ field: "environment", problem: "not_loaded" });
  });

  it("a mixed selection is shown as mixed (never the first row's value); bulk apply changes only the selection", () => {
    const { h, ids, only, eff } = three();
    only(ids[0]!);
    for (const f of ["srt", "json", "md"] as const) h.inputs.stageFormat(f, false);
    h.inputs.stageFormat("vtt", true);
    h.inputs.applyStaged();
    h.inputs.selectAll();
    const draft = h.form.store.get().draft;
    expect(summarize(h.inputs.selected(), draft, "formats")).toMatchObject({ count: 3, mixed: true, value: null, overridden: 1 });
    expect(summarize(h.inputs.selected(), draft, "language")).toMatchObject({ mixed: false, value: "ja", overridden: 0 });
    // Same set in another order is not "mixed".
    only(ids[1]!);
    h.inputs.stageFormat("json", false);
    h.inputs.applyStaged();
    h.inputs.stageFormat("json", true);
    h.inputs.applyStaged();
    expect(rows(h)[1]!.overrides.formats).toEqual(["srt", "md", "json"]);
    only(ids[1]!, ids[2]!);
    expect(summarize(h.inputs.selected(), h.form.store.get().draft, "formats").mixed).toBe(false);
    only(ids[1]!);
    h.inputs.stage({ language: "en" });
    h.inputs.applyStaged();
    expect([eff(0).language, eff(1).language, eff(2).language]).toEqual(["ja", "en", "ja"]);
    expect(eff(0).formats).toEqual(["vtt"]);
  });

  it("formats need at least one: a change leaving a row with none is refused (and is not 'use common')", () => {
    const { h, ids, only } = three();
    only(ids[0]!);
    for (const f of ["srt", "json", "md"] as const) h.inputs.stageFormat(f, false);
    expect(h.inputs.stagedProblems().formats).toBe(true);
    expect(h.inputs.applyStaged()).toBe(0);
    expect(rows(h)[0]!.overrides).toEqual({});
    // A staged edit belongs to its selection: changing the selection discards it.
    h.inputs.toggle(ids[1]!, true);
    expect(h.inputs.store.get().staged).toEqual({});
  });

  it("PR #43 P2-1: toggling one format on a mixed selection changes only that format in each row", () => {
    const { h, ids, only, eff } = three();
    // a: srt+json, b: srt+md (c keeps the common srt+json+md).
    only(ids[0]!);
    h.inputs.stageFormat("md", false);
    h.inputs.applyStaged();
    only(ids[1]!);
    h.inputs.stageFormat("json", false);
    h.inputs.applyStaged();
    only(ids[0]!, ids[1]!);
    const draft = () => h.form.store.get().draft;
    const state = (f: "srt" | "json" | "md" | "vtt") => formatState(h.inputs.selected(), draft(), h.inputs.store.get().staged, f);
    expect([state("srt"), state("json"), state("md")]).toEqual([
      { checked: true, indeterminate: false },
      { checked: false, indeterminate: true },
      { checked: false, indeterminate: true },
    ]);
    h.inputs.stageFormat("json", true);
    expect(state("json")).toEqual({ checked: true, indeterminate: false });
    expect(state("md")).toEqual({ checked: false, indeterminate: true }); // untouched: still mixed
    expect([eff(0).formats, eff(1).formats]).toEqual([["srt", "json"], ["srt", "md"]]); // nothing before Apply
    expect(h.inputs.applyStaged()).toBe(2);
    expect([eff(0).formats, eff(1).formats, eff(2).formats]).toEqual([["srt", "json"], ["srt", "md", "json"], ["srt", "json", "md"]]);
    // Turning one off removes only that one.
    h.inputs.stageFormat("srt", false);
    h.inputs.applyStaged();
    expect([eff(0).formats, eff(1).formats]).toEqual([["json"], ["md", "json"]]);
    // A change that would leave any row with no format is refused as a whole.
    h.inputs.stageFormat("json", false);
    expect(h.inputs.stagedProblems().formats).toBe(true);
    expect(h.inputs.applyStaged()).toBe(0);
    expect([eff(0).formats, eff(1).formats]).toEqual([["json"], ["md", "json"]]);
    // Changing the selection discards the unapplied change.
    h.inputs.toggle(ids[2]!, true);
    expect(h.inputs.store.get().staged).toEqual({});
  });

  it("a format change that leaves an inheriting row's set unchanged does not create an override", () => {
    const { h, ids, only } = three();
    only(ids[0]!);
    h.inputs.stageFormat("srt", true); // already on
    h.inputs.applyStaged();
    expect(rows(h)[0]!.overrides).toEqual({});
    expect(applyFormatChanges(["srt", "json"], { vtt: true, json: false })).toEqual(["srt", "vtt"]);
  });

  it("output: inherit the saved default, or a custom folder per row; an invalid custom folder is refused", () => {
    const { h, ids, only, eff } = three();
    expect(eff(0).outputDir).toBe(OUT);
    only(ids[1]!, ids[2]!);
    h.inputs.stage({ output: { kind: "custom", path: "relative/out" } });
    expect(h.inputs.stagedProblems().output).toBe("relative");
    expect(h.inputs.applyStaged()).toBe(0);
    h.inputs.stage({ output: { kind: "custom", path: `${OUT}/b` } });
    expect(h.inputs.applyStaged()).toBe(2);
    expect([eff(0).outputDir, eff(1).outputDir, eff(2).outputDir]).toEqual([OUT, `${OUT}/b`, `${OUT}/b`]);
    h.inputs.selectAll();
    expect(summarize(h.inputs.selected(), h.form.store.get().draft, "output")).toMatchObject({ mixed: true, overridden: 2 });
    only(ids[2]!);
    h.inputs.resetToCommon(["output"]);
    expect(eff(2).outputDir).toBe(OUT);
    // Settings are untouched: the per-row choice is not a settings PATCH.
    expect(h.set.get()).toEqual(settingsState(OUT));
  });

  it("no output default: the row has no output (blocked), nothing is composed from the input folder", () => {
    const h = b1Harness(environment("cuda"), "");
    h.form.choose("asrModel", "synthetic-a");
    h.inputs.setTyped(media("x.wav"));
    h.inputs.addTyped();
    expect(h.inputs.evaluateRow(rows(h)[0]!).issues).toContainEqual({ field: "output", problem: "empty" });
  });

  it("locked rows are excluded from bulk edits", async () => {
    const { h, ids } = three();
    const sent = h.gate.admit(h.inputs.evaluateRow(rows(h)[0]!).request!, { clientFileId: ids[0]!, origin: "single" });
    h.jobs.acceptAt(0);
    await sent;
    h.inputs.selectAll();
    expect(h.inputs.editableSelection().map((r) => r.clientFileId)).toEqual([ids[1], ids[2]]);
    h.inputs.stage({ language: "en" });
    expect(h.inputs.applyStaged()).toBe(2);
    expect(rows(h)[0]!.overrides).toEqual({});
  });
});

describe("privacy", () => {
  it("nothing is written to browser storage", () => {
    const h = b1Harness();
    h.inputs.setTyped(media("p.wav"));
    h.inputs.addTyped();
    expect(globalThis.localStorage?.length ?? 0).toBe(0);
    expect(globalThis.sessionStorage?.length ?? 0).toBe(0);
  });
});
