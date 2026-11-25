import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FetchLike } from "../../api/http";
import { parseState } from "../../api/worksets";
import { createFakeHost } from "../../host/fake";
import { deferred, json } from "../../test/fakeFetch";
import { fakeV1Server, presetWire, V1_POLICY, V1_PRESET } from "../../test/v1Server";
import { emptyWire, fakeWorksets } from "../../test/worksetsServer";
import { createCore } from "../core";
import { MAX_INPUTS } from "../inputCollection";
import { emptyWorksetState, project, restorePlan, stateKey } from "./worksetProjection";
import { checkName, SAVE_DEBOUNCE_MS, SAVE_MAX_WAIT_MS } from "./worksets";

const OUT = ["", "synthetic", "out"].join("/");
const path = (name: string) => ["", "synthetic", "media", name].join("/");

function setup(options: { exists?: boolean; presets?: Record<string, unknown>[] } = {}) {
  const v1 = fakeV1Server({ presets: options.presets });
  const ws = fakeWorksets({ exists: options.exists });
  const lines: string[] = [];
  const fetch: FetchLike = async (url, init) => {
    const p = new URL(url, "http://x").pathname;
    lines.push(`${init.method} ${p}`);
    if (p.startsWith("/api/v1/worksets")) return ws.handle(url, init);
    if (p.startsWith("/api/v1/")) return v1.fetch(url, init);
    if (p === "/api/settings") return json(200, { theme: "system", language: "ja", default_profile: null, default_output_dir: OUT });
    if (p === "/api/environment") return json(503, { detail: "synthetic" });
    throw new Error(`unexpected ${init.method} ${url}`);
  };
  const core = createCore({ fetch, host: createFakeHost(), apiMode: "v1-dev" });
  const worksets = core.v1!.worksets;
  return { core, worksets, ws, v1, lines };
}

async function settle(ms = SAVE_DEBOUNCE_MS + 10) {
  await vi.advanceTimersByTimeAsync(ms);
}

function add(core: ReturnType<typeof createCore>, ...names: string[]) {
  for (const name of names) {
    core.inputs.setTyped(path(name));
    core.inputs.addTyped();
  }
}

const b7Writes = (lines: string[]) => lines.filter((l) => !l.startsWith("GET ") && l.includes("/api/v1/") && !l.includes("/api/v1/worksets"));

describe("E-B projection (pure)", () => {
  it("projects only confirmed state, keeps inherit vs explicit empty, de-duplicates glossaries in first-use order", () => {
    const rows = [
      { clientFileId: "a", inputPath: "/a.wav", source: "picker" as const, selected: true, overrides: { glossary: ["X"] }, output: { kind: "inherit" as const }, attemptFloor: 0 },
      { clientFileId: "b", inputPath: "/b.wav", source: "drop" as const, selected: false, overrides: {}, output: { kind: "custom" as const, path: "/o" }, attemptFloor: 3, availability: "missing" as const },
      { clientFileId: "c", inputPath: "/c.wav", source: "typed" as const, selected: true, overrides: { glossary: [], formats: ["srt" as const], language: "en" as const }, output: { kind: "inherit" as const }, attemptFloor: 0 },
      { clientFileId: "d", inputPath: "/d.wav", source: "typed" as const, selected: true, overrides: { glossary: ["X"] }, output: { kind: "inherit" as const }, attemptFloor: 0 },
    ];
    const library = {
      preset: { phase: "error" as const, ref: { id: V1_PRESET, revision: 4 }, item: null, newerRevision: null, missing: true, error: null },
      policy: { phase: "idle" as const, ref: null, item: null, newerRevision: null, missing: false, error: null },
      screen: { language: null, formats: [] },
    };
    const state = project({ rows, primaryId: "c" }, { draft: { outputDir: OUT, outputSource: "picker", asrBackend: "x", asrModel: null, asrDevice: null, language: "ja", formats: ["srt"] } }, library);
    expect(state.glossaries).toEqual([["X"], []]);
    expect(state.inputs.map((i) => i.overrides.glossary)).toEqual([0, undefined, 1, 0]);
    expect(state.inputs[1]).toEqual({ input_path: "/b.wav", source: "drop", selected: false, output: { kind: "custom", path: "/o" }, overrides: {} });
    expect(state.primary_index).toBe(2);
    expect(state.preset_ref).toEqual({ id: V1_PRESET, revision: 4 }); // unreadable revision kept, never "latest"
    expect(state.common).toEqual({ language: null, formats: [], output_dir: OUT, output_source: "picker" });
    const following = project({ rows: [], primaryId: null }, { draft: { outputDir: OUT, outputSource: "saved_default", asrBackend: null, asrModel: null, asrDevice: null, language: "ja", formats: [] } }, library);
    expect(following.common).toMatchObject({ output_dir: "", output_source: null });
    const text = stateKey(state);
    for (const runtime of ["clientFileId", "attemptFloor", "availability", "\"typed\":", "staged", "token", "intent", "asrBackend", "job", "queue"]) expect(text).not.toContain(runtime);
    // Round trip through the wire parser and the restore plan.
    const plan = restorePlan(parseState(JSON.parse(text)));
    expect(plan.rows.map((r) => r.overrides.glossary)).toEqual([["X"], undefined, [], ["X"]]);
    expect(plan.rows[2]!.overrides).toEqual({ glossary: [], formats: ["srt"], language: "en" });
    expect(plan.primaryIndex).toBe(2);
    expect(plan.screen).toEqual({ language: null, formats: [] });
  });

  it("names: trimmed, 1–120 characters, no control characters", () => {
    expect(checkName("  案件A ")).toBe("案件A");
    expect(checkName("")).toBeNull();
    expect(checkName("a\nb")).toBeNull();
    expect(checkName("x".repeat(121))).toBeNull();
    expect(checkName("x".repeat(120))).toBe("x".repeat(120));
  });
});

describe("E-B worksets controller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("first run creates exactly 'Workset 1' once, restores it, and starts no B5/B7 write", async () => {
    const { core, worksets, ws, lines } = setup();
    await core.start();
    expect(ws.items.map((w) => w.name)).toEqual(["作業セット1"]);
    expect(worksets.store.get()).toMatchObject({ phase: "ready", current: { name: "作業セット1", revision: 1 }, save: { phase: "clean" } });
    expect(b7Writes(lines)).toEqual([]);
    // A second start (reload) never creates another one.
    await worksets.start();
    expect(ws.items).toHaveLength(1);
  });

  it("autosave coalesces many edits into one latest-state PUT after the debounce", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "a.wav", "b.wav", "c.wav");
    core.inputs.toggle(core.inputs.store.get().rows[1]!.clientFileId, false);
    expect(worksets.store.get().save.phase).toBe("dirty");
    expect(ws.applied).toEqual(["bootstrap"]);
    await settle();
    expect(ws.applied).toEqual(["bootstrap", "put"]);
    expect(worksets.store.get()).toMatchObject({ current: { revision: 2 }, save: { phase: "clean" } });
    const saved = ws.items[0]!.state as { inputs: { selected: boolean }[] };
    expect(saved.inputs.map((i) => i.selected)).toEqual([true, false, true]);
    // The typed-but-not-added field is not a saved change.
    core.inputs.setTyped(path("half"));
    await settle();
    expect(ws.applied).toHaveLength(2);
  });

  it("continuous edits are saved within the max wait, never one write per event", async () => {
    const { core, ws } = setup();
    await core.start();
    for (let i = 0; i < 20; i += 1) {
      add(core, `${i}.wav`);
      await vi.advanceTimersByTimeAsync(SAVE_MAX_WAIT_MS / 10);
    }
    await settle();
    const puts = ws.applied.filter((a) => a === "put").length;
    expect(puts).toBeGreaterThanOrEqual(2);
    expect(puts).toBeLessThanOrEqual(4);
    expect((ws.items[0]!.state.inputs as unknown[]).length).toBe(20);
  });

  it("reload restores the same rows, selection, primary, overrides, output and B5 refs, without any job write", async () => {
    const first = setup();
    await first.core.start();
    await first.core.v1!.library.select("preset", V1_PRESET);
    await first.core.v1!.library.select("policy", V1_POLICY);
    add(first.core, "a.wav", "b.wav");
    const rows = first.core.inputs.store.get().rows;
    first.core.inputs.setPrimary(rows[1]!.clientFileId);
    first.core.inputs.toggle(rows[0]!.clientFileId, false);
    first.core.v1!.library.setLanguage("en");
    first.core.transcription.edit({ outputDir: "/synthetic/custom-out" });
    await settle();
    const stored = structuredClone(first.ws.items[0]!.state);

    // A new page on the same store.
    const second = setup({ exists: true });
    second.ws.items.push(...first.ws.items);
    second.ws.active = first.ws.active;
    await second.core.start();
    const list = second.core.inputs.store.get();
    expect(list.rows.map((r) => [r.inputPath, r.selected])).toEqual([[path("a.wav"), false], [path("b.wav"), true]]);
    expect(list.primaryId).toBe(list.rows[1]!.clientFileId);
    expect(second.core.transcription.store.get().draft.outputDir).toBe("/synthetic/custom-out");
    await vi.advanceTimersByTimeAsync(0);
    const lib = second.core.v1!.library.store.get();
    expect(lib.preset.ref).toEqual({ id: V1_PRESET, revision: 1 });
    expect(lib.screen.language).toBe("en");
    expect(b7Writes(second.lines)).toEqual([]);
    expect(second.ws.applied).toEqual([]); // restoring alone writes nothing back
    expect(stateKey(second.worksets.store.get().current ? project(second.core.inputs.store.get(), second.core.transcription.store.get(), lib) : emptyWorksetState())).toBe(stateKey(parseState(stored)));
  });

  it("a deleted Preset revision stays referenced and blocks submission; nothing switches to the latest", async () => {
    const { core, ws } = setup({ exists: true, presets: [presetWire(V1_PRESET, 2)] });
    const state = { ...emptyWire(), preset_ref: { id: V1_PRESET, revision: 1 } };
    ws.items.push({ id: "ws_" + "a".repeat(32), name: "x", revision: 3, state, updatedAt: "2026-11-24T00:00:00Z" });
    ws.active = { id: ws.items[0]!.id, mode: "expert", revision: 1 };
    await core.start();
    await vi.advanceTimersByTimeAsync(0);
    const lib = core.v1!.library.store.get();
    // The fake B5 answers 404 for revision 1 (only revision 2 exists).
    expect(lib.preset).toMatchObject({ ref: { id: V1_PRESET, revision: 1 }, item: null, missing: true });
    expect(core.v1!.job.evaluate().issues).toContainEqual({ field: "preset", problem: "missing" });
    expect(core.shell.store.get().mode).toBe("expert"); // display mode restored
    await settle();
    expect(ws.applied).toEqual([]);
  });

  it("missing inputs are kept, marked, and block a start until fixed", async () => {
    const { core, ws, worksets } = setup();
    await core.start();
    add(core, "gone.wav", "here.wav");
    await settle();
    ws.missing.add(path("gone.wav"));
    await worksets.checkInputs();
    const rows = core.inputs.store.get().rows;
    expect(rows.map((r) => r.availability)).toEqual(["missing", "file"]);
    await settle();
    // Availability is runtime state: checking writes nothing.
    expect(ws.applied.filter((a) => a === "put")).toHaveLength(1);
    core.inputs.setPrimary(rows[0]!.clientFileId);
    expect(core.v1!.job.evaluate().issues).toContainEqual({ field: "input", problem: "missing_on_disk" });
    expect(core.v1!.batch.evaluate().issues).toContainEqual({ field: "input", rowId: rows[0]!.clientFileId, problem: "missing_on_disk" });
    // Removing the missing row (an explicit edit) makes the rest startable again.
    core.inputs.remove([rows[0]!.clientFileId]);
    expect(core.v1!.batch.evaluate().issues.some((i) => i.field === "input")).toBe(false);
  });

  it("412 from another window is a conflict: nothing is overwritten until the person chooses", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "mine.wav");
    ws.external(ws.items[0]!.id, { ...emptyWire(), inputs: [{ input_path: "/theirs.wav", source: "typed", selected: true, output: { kind: "inherit" }, overrides: {} }], primary_index: 0 });
    await settle();
    expect(worksets.store.get().save.phase).toBe("conflict");
    expect(worksets.store.get().conflict?.kind).toBe("remote_changed");
    expect((ws.items[0]!.state.inputs as { input_path: string }[])[0]!.input_path).toBe("/theirs.wav");
    // Switching away is refused while the conflict is open.
    expect(worksets.switchBlock()).toBe("conflict");
    worksets.overwriteRemote();
    await vi.advanceTimersByTimeAsync(0);
    expect((ws.items[0]!.state.inputs as { input_path: string }[])[0]!.input_path).toBe(path("mine.wav"));
    expect(worksets.store.get().save.phase).toBe("clean");
  });

  it("load theirs discards local edits and shows the stored version", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "mine.wav");
    ws.external(ws.items[0]!.id, { ...emptyWire(), inputs: [{ input_path: "/theirs.wav", source: "typed", selected: true, output: { kind: "inherit" }, overrides: {} }], primary_index: 0 });
    await settle();
    worksets.loadRemote();
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual(["/theirs.wav"]);
    await settle();
    expect(worksets.store.get().save.phase).toBe("clean");
  });

  it("a lost answer is reconciled by GET: the write is recognised, never duplicated", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "a.wav");
    ws.inject.set(`PUT /api/v1/worksets/${ws.items[0]!.id}`, { reply: new TypeError("network"), apply: true });
    await settle();
    expect(ws.applied.filter((a) => a === "put")).toHaveLength(1);
    expect(worksets.store.get()).toMatchObject({ save: { phase: "clean" }, current: { revision: 2 } });
  });

  it("a write that did not reach the store is retried under the same If-Match", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "a.wav");
    ws.inject.set(`PUT /api/v1/worksets/${ws.items[0]!.id}`, { reply: json(503, { schema_version: 1, error: { code: "store_busy", message_key: "worksets.store_busy", request_id: "r", retryable: true, issues: [] } }) });
    await settle();
    expect(worksets.store.get().save.phase).toBe("error");
    expect(worksets.switchBlock()).toBe("unsaved");
    await vi.advanceTimersByTimeAsync(2100);
    expect(worksets.store.get().save.phase).toBe("clean");
    expect(ws.applied.filter((a) => a === "put")).toHaveLength(1);
  });

  it("a too-large state is refused explicitly; the stored state stays and the same content is not resent", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "a.wav");
    const id = ws.items[0]!.id;
    ws.inject.set(`PUT /api/v1/worksets/${id}`, { reply: json(413, { schema_version: 1, error: { code: "state_too_large", message_key: "worksets.state_too_large", request_id: "r", retryable: false, issues: [] } }) });
    await settle();
    expect(worksets.store.get().save).toMatchObject({ phase: "error", error: { code: "state_too_large" } });
    const puts = ws.lines.filter((l) => l.startsWith("PUT /api/v1/worksets/ws_")).length;
    core.inputs.setTyped("");
    await settle(10_000);
    expect(ws.lines.filter((l) => l.startsWith("PUT /api/v1/worksets/ws_")).length).toBe(puts);
    expect(ws.items[0]!.revision).toBe(1);
  });

  it("switching flushes first, ignores late answers of the previous workset and restores the other", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "first.wav");
    expect(await worksets.create(false)).toBe(true);
    expect(ws.items).toHaveLength(2);
    expect((ws.items[0]!.state.inputs as unknown[]).length).toBe(1); // flushed before leaving
    expect(core.inputs.store.get().rows).toEqual([]);
    add(core, "second.wav");
    await settle();
    expect(await worksets.switchTo(ws.items[0]!.id)).toBe(true);
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("first.wav")]);
    expect(ws.active?.id).toBe(ws.items[0]!.id);
    await settle();
    expect((ws.items[1]!.state.inputs as { input_path: string }[]).map((i) => i.input_path)).toEqual([path("second.wav")]);
    expect((ws.items[0]!.state.inputs as unknown[]).length).toBe(1);
  });

  it("switching is refused while a save failed or a v1 submission is open", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    const other = ws.items[0]!.id;
    add(core, "x.wav");
    ws.inject.set(`PUT /api/v1/worksets/${ws.items[1]!.id}`, { reply: json(422, { schema_version: 1, error: { code: "state_invalid", message_key: "worksets.state_invalid", request_id: "r", retryable: false, issues: [] } }) });
    expect(await worksets.switchTo(other)).toBe(false);
    expect(worksets.store.get().op).toMatchObject({ phase: "error", block: "unsaved" });
    expect(core.inputs.store.get().rows).toHaveLength(1);
  });

  it("rename is a CAS save; delete removes only the workset and opens the successor", async () => {
    const { core, worksets, ws, lines } = setup();
    await core.start();
    expect(worksets.rename("  案件A  ")).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(ws.items[0]!.name).toBe("案件A");
    expect(worksets.rename("bad\nname")).toBe(false);
    await worksets.create(false);
    add(core, "temp.wav");
    const current = worksets.store.get().current!.id;
    expect(await worksets.remove(current)).toBe(true);
    expect(ws.items.map((w) => w.name)).toEqual(["案件A"]);
    expect(worksets.store.get().current?.id).toBe(ws.items[0]!.id);
    expect(core.inputs.store.get().rows).toEqual([]);
    expect(b7Writes(lines)).toEqual([]);
    // The last workset is never deleted.
    expect(await worksets.remove(ws.items[0]!.id)).toBe(false);
    expect(ws.items).toHaveLength(1);
  });

  it("the limit is explicit: no 21st workset and nothing is removed to make room", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    for (let i = 1; i < 20; i += 1) expect(await worksets.create(false)).toBe(true);
    expect(ws.items).toHaveLength(20);
    expect(await worksets.create(false)).toBe(false);
    expect(worksets.store.get().op.block).toBe("limit");
    expect(ws.items).toHaveLength(20);
    expect(new Set(ws.items.map((w) => w.name)).size).toBe(20);
  });

  it("edits made while the workset loads are not silently replaced", async () => {
    const { core, worksets, ws } = setup({ exists: true });
    const saved = { ...emptyWire(), inputs: [{ input_path: "/saved.wav", source: "typed", selected: true, output: { kind: "inherit" }, overrides: {} }], primary_index: 0 };
    ws.items.push({ id: "ws_" + "b".repeat(32), name: "x", revision: 2, state: saved, updatedAt: "2026-11-24T00:00:00Z" });
    ws.active = { id: ws.items[0]!.id, mode: "normal", revision: 1 };
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const original = ws.handle.bind(ws);
    ws.handle = async (url, init) => {
      if (init.method === "GET" && url.includes("/api/v1/worksets/ws_")) await gate;
      return original(url, init);
    };
    const started = core.start();
    await vi.advanceTimersByTimeAsync(0);
    add(core, "typed-meanwhile.wav");
    release();
    await started;
    expect(worksets.store.get().conflict?.kind).toBe("edited_during_load");
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("typed-meanwhile.wav")]);
    await settle();
    expect(ws.items[0]!.revision).toBe(2); // nothing overwritten
    worksets.loadRemote();
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual(["/saved.wav"]);
  });

  it("edits made before the restore starts (settings still loading) are not replaced either", async () => {
    const { core, worksets, ws } = setup({ exists: true });
    const saved = { ...emptyWire(), inputs: [{ input_path: "/saved.wav", source: "typed", selected: true, output: { kind: "inherit" }, overrides: {} }], primary_index: 0 };
    ws.items.push({ id: "ws_" + "c".repeat(32), name: "x", revision: 1, state: saved, updatedAt: "2026-11-24T00:00:00Z" });
    ws.active = { id: ws.items[0]!.id, mode: "normal", revision: 1 };
    add(core, "early.wav"); // before core.start() reaches the worksets
    await core.start();
    expect(worksets.store.get().conflict?.kind).toBe("edited_during_load");
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("early.wav")]);
    expect(ws.items[0]!.revision).toBe(1);
  });

  it("a path being typed during start-up is kept (not saved, not cleared)", async () => {
    const { core, ws } = setup();
    core.inputs.setTyped(path("typing"));
    await core.start();
    expect(core.inputs.store.get().typed).toBe(path("typing"));
    await settle();
    expect(ws.applied).toEqual(["bootstrap"]);
  });

  it("an unreadable store keeps the screen working in memory and never shows 'saved'", async () => {
    const { core, worksets, ws } = setup();
    ws.inject.set("GET /api/v1/worksets", { reply: json(503, { schema_version: 1, error: { code: "store_corrupt", message_key: "worksets.store_corrupt", request_id: "r", retryable: false, issues: [] } }) });
    await core.start();
    expect(worksets.store.get()).toMatchObject({ phase: "error", error: { code: "store_corrupt" }, current: null });
    add(core, "a.wav");
    await settle();
    expect(ws.applied).toEqual([]);
    // Nothing is claimed as saved, and leaving the page warns because nothing is.
    expect(worksets.hasUnsaved()).toBe(true);
  });

  it("restore replaces rows with new page-local ids (no old admission/file id can attach)", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "a.wav");
    await settle();
    const before = core.inputs.store.get().rows[0]!.clientFileId;
    const fileBefore = core.v1!.job.fileIdFor(before);
    await worksets.create(false);
    await worksets.switchTo(ws.items[0]!.id);
    const after = core.inputs.store.get().rows[0]!;
    expect(after.clientFileId).not.toBe(before);
    expect(core.v1!.job.fileIdFor(after.clientFileId)).not.toBe(fileBefore);
  });

  it("input list bounds still hold for a restored workset", () => {
    const { core } = setup();
    const rows = Array.from({ length: MAX_INPUTS + 1 }, (_, i) => ({ inputPath: path(`${i}.wav`), source: "typed" as const, selected: true, output: { kind: "inherit" as const }, overrides: {} }));
    expect(core.inputs.replaceAll(rows, 0)).toBe(false);
    expect(core.inputs.replaceAll(rows.slice(0, MAX_INPUTS), 99)).toBe(true);
    expect(core.inputs.store.get().rows).toHaveLength(MAX_INPUTS);
    expect(core.inputs.primary()?.inputPath).toBe(path("99.wav"));
  });
});

describe("E-B edits during an awaited operation are never discarded (PR #81 P1)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  /** Hold the first request matching `line` until `release()`; everything else answers at once. */
  function hold(ws: ReturnType<typeof fakeWorksets>, match: (line: string) => boolean) {
    const gate = deferred<void>();
    const original = ws.handle.bind(ws);
    let held = false;
    ws.handle = async (url, init) => {
      const line = `${init.method ?? "GET"} ${new URL(url, "http://x").pathname}`;
      if (!held && match(line)) {
        held = true;
        await gate.promise;
      }
      return original(url, init);
    };
    return () => gate.resolve();
  }

  it("switch: an input added while the other workset is read stays, is saved here, and the switch stops", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    const [first, second] = [ws.items[0]!.id, ws.items[1]!.id];
    expect(worksets.store.get().current?.id).toBe(second);
    const release = hold(ws, (l) => l === `GET /api/v1/worksets/${first}`);
    const switching = worksets.switchTo(first);
    await vi.advanceTimersByTimeAsync(0);
    expect(worksets.store.get().op).toMatchObject({ kind: "switch", phase: "pending" });
    add(core, "typed-while-switching.wav");
    core.inputs.toggle(core.inputs.store.get().rows[0]!.clientFileId, false);
    release();
    expect(await switching).toBe(false);
    expect(worksets.store.get().current?.id).toBe(second);
    expect(worksets.store.get().op).toMatchObject({ phase: "error", block: "edited_during_switch" });
    expect(core.inputs.store.get().rows.map((r) => [r.inputPath, r.selected])).toEqual([[path("typed-while-switching.wav"), false]]);
    await settle();
    expect(ws.items[1]!.state.inputs).toEqual([expect.objectContaining({ input_path: path("typed-while-switching.wav"), selected: false })]);
    expect(ws.items[0]!.state.inputs).toEqual([]);
    // Choosing again works once nothing changes in between.
    expect(await worksets.switchTo(first)).toBe(true);
    expect(core.inputs.store.get().rows).toEqual([]);
  });

  it("switch: a rename while the other workset is read is kept as well", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    const first = ws.items[0]!.id;
    const release = hold(ws, (l) => l === `GET /api/v1/worksets/${first}`);
    const switching = worksets.switchTo(first);
    await vi.advanceTimersByTimeAsync(0);
    expect(worksets.rename("途中の名前")).toBe(true);
    release();
    expect(await switching).toBe(false);
    await settle();
    expect(ws.items[1]!.name).toBe("途中の名前");
    expect(worksets.store.get().current?.id).toBe(ws.items[1]!.id);
  });

  it("create: the new workset is created but not opened when the screen changed meanwhile", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    const original = ws.items[0]!.id;
    const release = hold(ws, (l) => l === "POST /api/v1/worksets");
    const creating = worksets.create(false);
    await vi.advanceTimersByTimeAsync(0);
    add(core, "typed-while-creating.wav");
    release();
    expect(await creating).toBe(false);
    expect(ws.items).toHaveLength(2);
    expect(worksets.store.get().current?.id).toBe(original);
    expect(worksets.store.get().op.block).toBe("edited_during_create");
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("typed-while-creating.wav")]);
    await settle();
    expect((ws.items[0]!.state.inputs as unknown[]).length).toBe(1);
    expect(worksets.store.get().items.map((i) => i.id)).toContain(ws.items[1]!.id);
  });

  it("restore: edits made while the checkpoint is restored stay on screen as an explicit conflict", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    add(core, "saved.wav");
    await settle();
    const id = ws.items[0]!.id;
    const release = hold(ws, (l) => l.endsWith("/restore"));
    const restoring = worksets.restoreRecovery(1);
    await vi.advanceTimersByTimeAsync(0);
    add(core, "typed-while-restoring.wav");
    release();
    expect(await restoring).toBe(false);
    expect(worksets.store.get().conflict?.kind).toBe("remote_changed");
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("saved.wav"), path("typed-while-restoring.wav")]);
    expect(ws.applied).toContain("restore");
    worksets.overwriteRemote();
    await vi.advanceTimersByTimeAsync(0);
    expect((ws.items.find((w) => w.id === id)!.state.inputs as unknown[]).length).toBe(2);
  });

  it("delete: edits after the confirmation are not lost; the screen is offered as a new workset", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    const doomed = ws.items[1]!.id;
    const release = hold(ws, (l) => l === `DELETE /api/v1/worksets/${doomed}`);
    const removing = worksets.remove(doomed);
    await vi.advanceTimersByTimeAsync(0);
    add(core, "typed-while-deleting.wav");
    release();
    expect(await removing).toBe(true);
    expect(ws.items.map((w) => w.id)).not.toContain(doomed);
    expect(worksets.store.get().conflict?.kind).toBe("deleted");
    expect(core.inputs.store.get().rows.map((r) => r.inputPath)).toEqual([path("typed-while-deleting.wav")]);
    await worksets.saveAsNew();
    await settle();
    const created = ws.items.find((w) => (w.state.inputs as unknown[]).length === 1);
    expect(created).toBeDefined();
    expect(worksets.store.get().current?.id).toBe(created!.id);
  });
});

describe("E-B the remembered active workset never moves back (PR #81 P2)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("W1 → W2 → W3 with W2's answer lost and its request applied late: the server keeps W3", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    await worksets.create(false);
    await vi.advanceTimersByTimeAsync(0);
    const [w1, w2, w3] = ws.items.map((w) => w.id);
    await worksets.switchTo(w1!);
    await vi.advanceTimersByTimeAsync(0);
    expect(ws.active?.id).toBe(w1);
    // Hold W2's PUT: the client sees no answer; the server receives it only later.
    const original = ws.handle.bind(ws);
    let late: (() => Promise<Response>) | null = null;
    ws.handle = async (url, init) => {
      if (late === null && init.method === "PUT" && url.endsWith("/api/v1/worksets/active") && String(init.body).includes(w2!)) {
        late = () => original(url, init);
        throw new TypeError("synthetic connection loss");
      }
      return original(url, init);
    };
    await worksets.switchTo(w2!);
    await vi.advanceTimersByTimeAsync(0);
    expect(worksets.store.get().active.phase).toBe("error");
    expect(worksets.hasUnsaved()).toBe(true);
    await worksets.switchTo(w3!);
    await vi.advanceTimersByTimeAsync(2100);
    expect(ws.active?.id).toBe(w3);
    expect(worksets.store.get().active.phase).toBe("clean");
    // The old request finally reaches the server: refused by CAS, nothing moves back.
    const answer = await late!();
    expect(answer.status).toBe(412);
    expect(ws.active?.id).toBe(w3);
    const puts = ws.lines.filter((l) => l === "PUT /api/v1/worksets/active");
    expect(puts.length).toBeGreaterThanOrEqual(2);
  });

  it("requests are sent one at a time and only the latest wish is confirmed", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    await worksets.create(false);
    await vi.advanceTimersByTimeAsync(0);
    const original = ws.handle.bind(ws);
    let inFlight = 0;
    let maxInFlight = 0;
    ws.handle = async (url, init) => {
      if (init.method === "PUT" && url.endsWith("/active")) {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 50));
        inFlight -= 1;
      }
      return original(url, init);
    };
    core.shell.setMode("expert");
    await vi.advanceTimersByTimeAsync(600);
    core.shell.setMode("normal");
    await vi.advanceTimersByTimeAsync(10);
    core.shell.setMode("expert");
    await vi.advanceTimersByTimeAsync(2000);
    expect(maxInFlight).toBe(1);
    expect(ws.active?.mode).toBe("expert");
    expect(worksets.store.get().active.phase).toBe("clean");
  });

  it("a failing pointer write is shown and retried, never reported as saved", async () => {
    const { core, worksets, ws } = setup();
    await core.start();
    const busy = json(503, { schema_version: 1, error: { code: "store_busy", message_key: "worksets.store_busy", request_id: "r", retryable: true, issues: [] } });
    ws.inject.set("PUT /api/v1/worksets/active", { reply: busy });
    core.shell.setMode("expert");
    await vi.advanceTimersByTimeAsync(600);
    expect(worksets.store.get().active).toMatchObject({ phase: "error", error: { code: "store_busy" } });
    expect(worksets.hasUnsaved()).toBe(true);
    expect(ws.active?.mode).toBe("normal");
    await vi.advanceTimersByTimeAsync(2100);
    expect(ws.active?.mode).toBe("expert");
    expect(worksets.store.get().active.phase).toBe("clean");
  });
});
