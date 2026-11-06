import { describe, expect, it } from "vitest";
import { en } from "../i18n/en";
import { ja } from "../i18n/ja";
import {
  CATEGORIES,
  CONTRACT_PATH,
  DEFAULT_COMMON,
  engineCandidates,
  FIELDS,
  fieldsOf,
  normalizeEngine,
  optionsFor,
  SETTING_KEYS,
  toContractShape,
  validateSettings,
} from "./settingsSchema";

const s = (patch: Partial<typeof DEFAULT_COMMON> = {}) => ({ ...DEFAULT_COMMON, ...patch });

describe("processing settings schema (iteration 4, owner memo #19)", () => {
  it("covers ASR, prompt/glossary, diarization, output and advanced; every field has a ja/en label", () => {
    expect(CATEGORIES).toEqual(["asr", "prompt", "diarization", "output", "advanced"]);
    for (const c of CATEGORIES) expect(fieldsOf(c).length, c).toBeGreaterThan(0);
    for (const key of ["initialPrompt", "glossary", "vad", "beamSize", "wordTimestamps", "conditionOnPreviousText", "wcThreads", "wcTemperature", "wcDtw", "genaiMaxNewTokens"] as const)
      expect(FIELDS.some((f) => f.key === key), key).toBe(true);
    for (const key of SETTING_KEYS) {
      expect(`field.${key}` in ja, key).toBe(true);
      expect(`field.${key}` in en, key).toBe(true);
      expect(key in CONTRACT_PATH, key).toBe(true);
    }
  });

  it("every select option has a label or is a product name shown as is", () => {
    for (const def of FIELDS.filter((f) => f.kind === "select")) {
      for (const backend of ["auto", "faster-whisper", "whisper-cpp", "openvino-genai"] as const) {
        for (const v of optionsFor(def.key, s({ backend }))) {
          const labelled = `opt.${def.key}.${v}` in ja || `model.${v}` in ja || def.key === "model" || def.key === "computeType";
          expect(labelled, `${def.key}=${v}`).toBe(true);
        }
      }
    }
  });

  it("backend-specific fields are shown only for their backend", () => {
    const visible = (backend: (typeof DEFAULT_COMMON)["backend"]) => FIELDS.filter((f) => !f.visible || f.visible(s({ backend }))).map((f) => f.key);
    expect(visible("whisper-cpp")).toEqual(expect.arrayContaining(["wcThreads", "wcTemperature", "wcDtw", "wcNoSpeechThreshold"]));
    expect(visible("whisper-cpp")).not.toContain("computeType");
    expect(visible("faster-whisper")).toContain("computeType");
    expect(visible("faster-whisper")).not.toContain("wcThreads");
    expect(visible("openvino-genai")).toContain("genaiMaxNewTokens");
    expect(visible("auto")).not.toEqual(expect.arrayContaining(["computeType", "wcThreads", "genaiMaxNewTokens"]));
  });

  it("a backend switch keeps model / device / compute type within that backend's candidates", () => {
    const fw = normalizeEngine(s({ backend: "faster-whisper", model: "demo-model-balanced", device: "auto", computeType: "float16" }));
    expect(engineCandidates("faster-whisper").models).toContain(fw.model);
    expect(fw.computeType).toBe("float16");
    const wc = normalizeEngine({ ...fw, backend: "whisper-cpp", device: "cuda" });
    expect(engineCandidates("whisper-cpp").models).toContain(wc.model);
    expect(wc.device).toBe("cuda");
    expect(wc.computeType).toBe("auto");
    const ov = normalizeEngine({ ...wc, backend: "openvino-genai" });
    expect(ov.device).toBe("auto"); // CUDA is not an OpenVINO candidate in the fixture
  });

  it("validation: ranges, integers, glossary entries, prompt length, speaker range order", () => {
    expect(validateSettings(DEFAULT_COMMON)).toEqual([]);
    expect(validateSettings(s({ beamSize: 0 })).map((i) => i.code)).toEqual(["range"]);
    expect(validateSettings(s({ beamSize: 2.5 })).map((i) => i.code)).toEqual(["integer"]);
    expect(validateSettings(s({ vadThreshold: 1.5 })).map((i) => i.key)).toEqual(["vadThreshold"]);
    expect(validateSettings(s({ vad: false, vadThreshold: 1.5 }))).toEqual([]); // inactive field: not checked
    expect(validateSettings(s({ glossary: ["Utteran", "utteran", " ", "x".repeat(257)] })).map((i) => `${i.code}@${i.index}`)).toEqual(["duplicate@1", "empty@2", "tooLong@3"]);
    expect(validateSettings(s({ initialPrompt: "a".repeat(32769) })).map((i) => i.code)).toEqual(["tooLong"]);
    expect(validateSettings(s({ speakerMode: "range", minSpeakers: 5, maxSpeakers: 2 })).map((i) => i.code)).toEqual(["order"]);
    expect(validateSettings(s({ diarization: false, speakerMode: "range", minSpeakers: 5, maxSpeakers: 2 }))).toEqual([]);
    expect(validateSettings(s({ wcThreads: -1 }))).toEqual([]); // whisper.cpp not selected
    expect(validateSettings(s({ backend: "whisper-cpp", wcThreads: -1 })).map((i) => i.key)).toEqual(["wcThreads"]);
    expect(validateSettings(s({ formats: [] })).map((i) => i.code)).toEqual(["required"]);
  });

  it("contract shape: only the selected backend's section, one speaker form, no device", () => {
    const fw = toContractShape(s({ backend: "faster-whisper", model: "small", computeType: "int8" })) as { asr: Record<string, unknown> };
    expect(fw.asr.faster_whisper).toEqual({ compute_type: "int8" });
    expect(fw.asr.whisper_cpp).toBeUndefined();
    expect("device" in fw.asr).toBe(false); // the device is not part of ProcessingSettings
    expect(CONTRACT_PATH.device).toBeNull();
    const fixed = toContractShape(s({ speakerMode: "fixed", numSpeakers: 3 })) as { diarization: Record<string, unknown> };
    expect(fixed.diarization).toMatchObject({ num_speakers: 3, min_speakers: null, max_speakers: null });
    const range = toContractShape(s({ speakerMode: "range", minSpeakers: 2, maxSpeakers: 5 })) as { diarization: Record<string, unknown> };
    expect(range.diarization).toMatchObject({ num_speakers: null, min_speakers: 2, max_speakers: 5 });
    const empty = toContractShape(DEFAULT_COMMON) as { asr: Record<string, unknown> };
    expect(empty.asr.initial_prompt).toBeNull();
  });
});
