/**
 * Deterministic synthetic fixtures for the UI-3-0 prototype. Nothing here refers to a real file,
 * path, recording, transcript, model inventory or device. Names are plain file names (no folders).
 */
export const OUTPUT_FORMATS = ["srt", "vtt", "json", "md", "txt"] as const;
export type OutputFormat = (typeof OUTPUT_FORMATS)[number];

/** Example labels only. The real candidates come from the backend (B4/B7), never from this list. */
export const DEMO_MODELS = ["demo-model-accurate", "demo-model-balanced", "demo-model-fast"] as const;
export type DemoModel = (typeof DEMO_MODELS)[number];

export const LANGUAGES = ["auto", "ja", "en"] as const;
export type InputLanguage = (typeof LANGUAGES)[number];

export const SPEAKER_COUNTS = ["auto", "1", "2", "3", "4", "5", "6"] as const;
export type SpeakerCount = (typeof SPEAKER_COUNTS)[number];

export interface ProcessingSettings {
  model: DemoModel;
  language: InputLanguage;
  diarization: boolean;
  speakers: SpeakerCount;
  formats: OutputFormat[];
}
export type SettingKey = keyof ProcessingSettings;
export const SETTING_KEYS: readonly SettingKey[] = ["model", "language", "diarization", "speakers", "formats"];

export interface DemoInput {
  id: string;
  name: string;
  /** Lower-case extension without the dot. */
  format: string;
  /** Synthetic duration in seconds; null when the format is not audio/video. */
  durationSec: number | null;
  supported: boolean;
}

export const DEFAULT_COMMON: ProcessingSettings = {
  model: "demo-model-balanced",
  language: "ja",
  diarization: true,
  speakers: "auto",
  formats: ["srt", "json"],
};

const SUPPORTED = new Set(["wav", "mp3", "m4a", "flac", "mp4", "mkv", "mov"]);

function input(id: string, name: string, durationSec: number | null): DemoInput {
  const format = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  return { id, name, format, durationSec, supported: SUPPORTED.has(format) };
}

/** The initial list of the default scenario (long name and unsupported format on purpose). */
export function initialInputs(): DemoInput[] {
  return [
    input("in-01", "sample-meeting-01.wav", 754),
    input("in-02", "interview-sample-02.wav", 1312),
    input("in-03", "quarterly-planning-review-session-recording-with-a-very-long-synthetic-name-03.m4a", 3605),
    input("in-04", "lecture-sample-04.mp4", 2410),
    input("in-05", "notes-sample-05.txt", null),
  ];
}

/** "Add file" and "Add folder" draw from this pool in order (deterministic, never a real picker). */
export const ADD_POOL: readonly [string, number | null][] = [
  ["sample-call-06.mp3", 418],
  ["sample-seminar-07.flac", 5021],
  ["folder-sample-part-08.wav", 600],
  ["folder-sample-part-09.wav", 600],
  ["folder-sample-part-10.wav", 412],
  ["sample-memo-11.m4a", 95],
  ["sample-briefing-12.mkv", 1820],
];

export function poolInput(index: number): DemoInput {
  const entry = ADD_POOL[index % ADD_POOL.length] ?? ADD_POOL[0]!;
  const round = Math.floor(index / ADD_POOL.length);
  const name = round === 0 ? entry[0] : entry[0].replace(/(\.\w+)$/, `-copy${round}$1`);
  return input(`add-${String(index + 1).padStart(2, "0")}`, name, entry[1]);
}

/** mm:ss or h:mm:ss for a synthetic duration. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  return h > 0 ? `${h}:${mm}:${String(s).padStart(2, "0")}` : `${mm}:${String(s).padStart(2, "0")}`;
}
