/**
 * Engines and models the UI-3-0 setup wizard offers (iteration 5, owner memo #30).
 *
 * SYNTHETIC FIXTURE, kept in this one file so no candidate list is scattered over components.
 * Model ids, display names and approximate sizes follow the model catalog
 * (`src/utteran/models/catalog.py`: `faster-whisper:large-v3-turbo`, `faster-whisper:large-v3`,
 * `faster-whisper:kotoba-whisper-v2.0`, …). A model being in the catalog does NOT mean the
 * execution path runs it today (B7's worker supports a subset); the product takes the candidates,
 * the device/model compatibility and the recommendation from the backend (B4 inventory, B8 plan).
 * The device rules below are prototype examples, not measured facts.
 */
export type SetupDevice = "gpu" | "cpu";
/** B8 `ProfileKind` of each device choice of the prototype. */
export const PROFILE_KIND: Record<SetupDevice, "cuda" | "cpu"> = { gpu: "cuda", cpu: "cpu" };
/** B8 `SetupPlanRequest.model_refs` accepts 1–8 refs. */
export const MAX_MODEL_REFS = 8;

export type SetupEngine = "faster-whisper" | "whisper-cpp" | "openvino-genai";
export interface EngineOption {
  id: SetupEngine;
  /** Whether the prototype lets this engine be chosen. Others are listed but never claimed usable. */
  selectable: boolean;
}
export const SETUP_ENGINES: readonly EngineOption[] = [
  { id: "faster-whisper", selectable: true },
  // In the catalog, but the setup and execution paths for them are not part of this prototype.
  { id: "whisper-cpp", selectable: false },
  { id: "openvino-genai", selectable: false },
];

export interface SetupModel {
  /** Catalog model id (the part after "engine:"). */
  id: string;
  engine: SetupEngine;
  displayName: string;
  /** Approximate download in MB from the catalog's `approximate_size_bytes`; null = unknown. */
  sizeMb: number | null;
  /** Devices this model is offered for in the prototype (example rule). */
  devices: readonly SetupDevice[];
  /** i18n key suffix of the one-line note (setupModel.note.<note>), if any. */
  note: "balanced" | "accurate" | "japanese" | "light" | "middle" | null;
}

const MB = 1_000_000;
const GIB = 1024 ** 3;
const mb = (bytes: number) => Math.round(bytes / MB);

export const SETUP_MODELS: readonly SetupModel[] = [
  { id: "large-v3-turbo", engine: "faster-whisper", displayName: "Whisper large-v3-turbo", sizeMb: mb(1.6 * GIB), devices: ["gpu", "cpu"], note: "balanced" },
  // Example rule: large-v3 is offered for the GPU only (on a CPU it is impractically slow).
  { id: "large-v3", engine: "faster-whisper", displayName: "Whisper large-v3", sizeMb: mb(3.1 * GIB), devices: ["gpu"], note: "accurate" },
  { id: "kotoba-whisper-v2.0", engine: "faster-whisper", displayName: "Kotoba-Whisper v2.0", sizeMb: mb(1.52 * GIB), devices: ["gpu", "cpu"], note: "japanese" },
  { id: "medium", engine: "faster-whisper", displayName: "Whisper medium", sizeMb: mb(1.53 * GIB), devices: ["gpu", "cpu"], note: "middle" },
  { id: "small", engine: "faster-whisper", displayName: "Whisper small", sizeMb: 486, devices: ["gpu", "cpu"], note: "light" },
];

/** What the standard setup proposes (example rule; the product asks the backend). */
export const RECOMMENDED_MODEL: Record<SetupDevice, string> = { gpu: "large-v3-turbo", cpu: "small" };

/** Synthetic sizes of the parts that are not ASR models. */
export const RUNTIME_SIZE: Record<SetupDevice, { downloadMb: number; diskMb: number }> = {
  gpu: { downloadMb: 2400, diskMb: 5200 },
  cpu: { downloadMb: 600, diskMb: 1400 },
};
export const REPAIR_SIZE: Record<SetupDevice, { downloadMb: number; diskMb: number }> = {
  gpu: { downloadMb: 900, diskMb: 2100 },
  cpu: { downloadMb: 300, diskMb: 700 },
};
export const VAD_SIZE = { downloadMb: 2, diskMb: 2 };
export const DIARIZATION_SIZE = { downloadMb: 60, diskMb: 60 };

export function setupModel(id: string): SetupModel | null {
  return SETUP_MODELS.find((m) => m.id === id) ?? null;
}

/** Models of one engine offered for one device, in display order. */
export function modelsFor(engine: SetupEngine, device: SetupDevice): SetupModel[] {
  return SETUP_MODELS.filter((m) => m.engine === engine && m.devices.includes(device));
}

/** Whether a model may be chosen for a device (the UI disables every other combination). */
export function modelAllowed(id: string, engine: SetupEngine, device: SetupDevice): boolean {
  const m = setupModel(id);
  return m !== null && m.engine === engine && m.devices.includes(device);
}

/** "faster-whisper:large-v3-turbo" — the B8 `ModelRef` form. */
export function modelRef(engine: SetupEngine, id: string): string {
  return `${engine}:${id}`;
}

export function modelName(id: string): string {
  return setupModel(id)?.displayName ?? id;
}
