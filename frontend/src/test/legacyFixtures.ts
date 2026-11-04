import type { LegacyEnvironment, LegacySettings } from "../api/legacy";

/** Synthetic legacy DTOs (no real paths, names or tokens). */
export function environment(profile: string): LegacyEnvironment {
  return {
    profiles: [
      { name: "cpu", exists: true, compatible: true },
      { name: "cuda", exists: true, compatible: true },
    ],
    activeProfile: profile,
    asr: [{ id: "faster-whisper", label: "faster-whisper", modelCount: 2, deviceIds: [profile === "cuda" ? "cuda:0" : "cpu"] }],
    diarization: [],
    languageCount: 3,
    errors: [],
    warnings: [],
  };
}

export function settings(patch: Partial<LegacySettings> = {}): LegacySettings {
  return { theme: "system", language: "ja", defaultProfile: null, tokenConfigured: false, ...patch };
}
