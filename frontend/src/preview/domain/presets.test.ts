import { describe, expect, it } from "vitest";
import { BUILTIN_PRESETS, exportPreset, IMPORT_SAMPLES, initialUserPresets, parsePresetDocument, presetDiff, presetKeys, uniqueName } from "./presets";
import { DEFAULT_COMMON } from "./settingsSchema";

const sample = (id: string) => parsePresetDocument(IMPORT_SAMPLES.find((s) => s.id === id)!.text);

describe("presets (iteration 4, owner memo #20; in memory, exchange format is a design candidate)", () => {
  it("a preset changes exactly its own keys", () => {
    const subtitles = BUILTIN_PRESETS.find((p) => p.id === "builtin-subtitles")!;
    expect(presetKeys(subtitles)).toEqual(["language", "wordTimestamps", "diarization", "formats"]);
    expect(presetDiff(DEFAULT_COMMON, subtitles)).toEqual(["wordTimestamps", "diarization", "formats"]);
    expect(uniqueName("a", ["a", "a (2)"])).toBe("a (3)");
  });

  it("export: versioned JSON, never the device; prompt and glossary only when asked", () => {
    const user = initialUserPresets()[0]!;
    const withoutText = exportPreset({ ...user, values: { ...user.values, device: "cuda" } }, user.name, { includePrivateText: false });
    const doc = JSON.parse(withoutText.json);
    expect(doc).toMatchObject({ format: "utteran-preset", format_version: 1, name: user.name });
    expect(doc.settings.asr.initial_prompt).toBeUndefined();
    expect(doc.settings.asr.glossary).toBeUndefined();
    expect(withoutText.json).not.toContain("cuda");
    expect(withoutText.omitted).toEqual(["device", "initialPrompt", "glossary"]);
    expect(doc.settings.diarization).toMatchObject({ num_speakers: null, min_speakers: 2, max_speakers: 6 });
    const withText = exportPreset(user, user.name, { includePrivateText: true });
    expect(JSON.parse(withText.json).settings.asr.glossary).toEqual(["Utteran", "議事録", "アクションアイテム"]);
    expect(withText.fileName).toMatch(/\.utteran-preset\.json$/);
  });

  it("export → import round trip keeps the values", () => {
    const user = initialUserPresets()[0]!;
    const back = parsePresetDocument(exportPreset(user, user.name, { includePrivateText: true }).json);
    expect(back.ok).toBe(true);
    expect(back.values).toEqual(user.values);
  });

  it("import samples: valid, duplicate name, old version, broken JSON, unsupported values / secrets", () => {
    const valid = sample("valid");
    expect(valid).toMatchObject({ ok: true, name: "インタビュー（2人）" });
    expect(valid.values).toMatchObject({ language: "ja", diarization: true, speakerMode: "fixed", numSpeakers: 2, formats: ["txt", "srt"] });
    expect(sample("duplicate")).toMatchObject({ ok: true, name: initialUserPresets()[0]!.name });
    expect(sample("oldVersion").issues.map((i) => i.code)).toEqual(["version"]);
    expect(sample("broken").issues.map((i) => i.code)).toEqual(["notJson"]);
    const bad = sample("unsupported");
    expect(bad.ok).toBe(false);
    expect(bad.issues.map((i) => i.code).sort()).toEqual(["badValue", "secretField"]);
    expect(bad.values).toEqual({}); // nothing is partially imported
    expect(parsePresetDocument(JSON.stringify({ format: "other" })).issues[0]!.code).toBe("notPreset");
    const outOfRange = parsePresetDocument(JSON.stringify({ format: "utteran-preset", format_version: 1, name: "x", settings: { asr: { beam_size: 500 } } }));
    expect(outOfRange.issues.map((i) => i.code)).toEqual(["invalid"]);
  });
});
