/**
 * Minimal, value-checked projection of the legacy `GET /api/environment` response (UI-3 S2).
 * Contract source: src/utteran_gui/environment.py `EnvironmentService.snapshot()` and
 * `derive_options()` (code is the authority).
 *
 * This is an *observation* the profile CLI reported at that moment. It is NOT a B4
 * `InventorySnapshot` and NOT ready evidence (G-01): nothing here may be turned into "ready",
 * "runs", a smoke result or a recommendation.
 *
 * Kept: only what the S2 screens show. Dropped on purpose: every path (`profiles[].path`,
 * `model_path`, `models[].path`, `openvino_models[].xml_path`, `native.manifest`), server-made
 * capability prose (`execution_label`, `recommendation_reason`, `options.defaults`), probe
 * `detail`/`error` texts and the raw `errors[]` strings (they can contain machine paths, e.g.
 * "Profile Python is not available: <path>"); errors become fixed codes instead.
 *
 * Fail closed: `profiles`, `active_profile`, `options`, `options.asr`, `options.diarization` and
 * `errors` must have the right shape or the whole response is `malformed`. Every other field is
 * optional: missing, `null` or broken values become "unknown"/"not reported" and never break
 * the screen.
 */
import { bool, list, record, ShapeError, str } from "./guards";

/** Upper bounds for anything shown (strings are cut, lists are capped). */
export const LIMITS = { text: 120, items: 32 } as const;

export type CompatibilityReason =
  | "current_lock_missing"
  | "profile_manifest_missing"
  | "profile_manifest_invalid"
  | "dependency_lock_changed"
  | "profile_extras_changed"
  | "profile_path_changed"
  | "other";

export interface LegacyProfile {
  name: string;
  exists: boolean;
  /** `null` = the server could not tell (never treated as compatible). */
  compatible: boolean | null;
  compatibilityReason: CompatibilityReason | null;
}

export interface CandidateItem {
  id: string;
  label: string;
}

/** An `options.asr`/`options.diarization` entry: advertised by the CLI, unverified. */
export interface LegacyOption {
  id: string;
  label: string;
  /** Models listed *inside this candidate* (not the installed-model count). */
  modelCount: number;
  models: CandidateItem[];
  deviceIds: string[];
  devices: CandidateItem[];
}

export type AcceleratorRuntime = "ctranslate2_cuda" | "pytorch_cuda" | "pytorch_xpu";

/** One accelerator as one runtime's probe saw it. `usable` is that probe's observation only. */
export interface AcceleratorObservation {
  runtime: AcceleratorRuntime;
  index: number | null;
  name: string;
  usable: boolean;
}

export type ProbeStatus = "completed" | "timeout" | "error" | "unknown";

export interface ProbeObservation {
  name: string;
  status: ProbeStatus;
  cached: boolean;
}

export interface DeviceObservation {
  cpu: { logicalCores: number | null; physicalCores: number | null };
  ctranslate2Available: boolean | null;
  pytorchAvailable: boolean | null;
  accelerators: AcceleratorObservation[];
  /** `devices.openvino.values` (e.g. "CPU", "GPU.0"); `null` = not reported. */
  openvino: { available: boolean | null; devices: string[] } | null;
  /** `devices.native` (whisper.cpp builds in the manifest); `null` = not reported. */
  native: { built: boolean | null; variants: string[] } | null;
  probes: ProbeObservation[];
  probeCacheHit: boolean | null;
}

export interface InstalledModel {
  backend: string;
  label: string;
}

/** The fixed probe names the legacy service runs, recovered from its own timeout message. */
export type ProbeId = "profiles" | "devices" | "models" | "native" | "model_path" | "openvino_models" | "profile_runtime";

export interface EnvironmentIssue {
  /** timeout = a CLI call ran out of time; failed = it failed; malformed = part of the answer was unreadable. */
  kind: "timeout" | "failed" | "malformed";
  probe: ProbeId | "options" | null;
  seconds: number | null;
}

export type GuidanceCode = "native_not_built" | "ggml_model_missing" | "other";

export interface LegacyEnvironment {
  profiles: LegacyProfile[];
  /** The profile the *server* used. Not the same as what was requested (or saved as default). */
  activeProfile: string | null;
  asr: LegacyOption[];
  diarization: LegacyOption[];
  languageCount: number;
  /** `null` = no device report (no active profile, or the probe failed). */
  devices: DeviceObservation | null;
  /** Rows of `models[]` with `installed === true`; `null` = not obtained (no active profile) or unreadable. */
  installedModels: InstalledModel[] | null;
  installedModelCount: number | null;
  /** Variants `native status` reported as runnable; `null` = not reported. */
  nativeRunnable: string[] | null;
  /** OpenVINO encoder IRs (`openvino_models`), counted separately from models; `null` = not reported. */
  openvinoIr: { installed: number; total: number } | null;
  guidance: GuidanceCode[];
  issues: EnvironmentIssue[];
  /** Number of server `profile_warnings`; the texts are rebuilt from `profiles` in the UI language. */
  profileWarningCount: number;
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$/;
// C0/C1 control characters and bidi overrides are removed from anything displayed.
const UNSAFE = /[\u0000-\u001f\u007f-\u009f‪-‮⁦-⁩]/g;

function obj(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function arr(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null;
}

function tri(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Display text: string only, control characters removed, at most LIMITS.text characters. */
export function safeText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.replace(UNSAFE, " ").trim();
  if (cleaned === "") return null;
  return cleaned.length > LIMITS.text ? `${cleaned.slice(0, LIMITS.text - 1)}…` : cleaned;
}

function identifier(value: unknown): string | null {
  return typeof value === "string" && ID.test(value) ? value : null;
}

function capped<T>(items: T[]): T[] {
  return items.slice(0, LIMITS.items);
}

const REASONS: readonly CompatibilityReason[] = [
  "current_lock_missing",
  "profile_manifest_missing",
  "profile_manifest_invalid",
  "dependency_lock_changed",
  "profile_extras_changed",
  "profile_path_changed",
];

function reason(value: unknown): CompatibilityReason | null {
  if (value === null || value === undefined) return null;
  return (REASONS as readonly unknown[]).includes(value) ? (value as CompatibilityReason) : "other";
}

function items(value: unknown): CandidateItem[] {
  return capped(
    (arr(value) ?? []).flatMap((entry) => {
      const r = obj(entry);
      const id = identifier(r?.id);
      if (!r || id === null) return [];
      return [{ id, label: safeText(r.label) ?? id }];
    }),
  );
}

function option(value: unknown): LegacyOption | null {
  const r = obj(value);
  const id = identifier(r?.id);
  if (!r || id === null) return null;
  const models = arr(r.models) ?? [];
  const devices = items(r.devices);
  return {
    id,
    label: safeText(r.label) ?? id,
    modelCount: models.length,
    models: items(models),
    deviceIds: devices.map((d) => d.id),
    devices,
  };
}

function accelerators(value: unknown, runtime: AcceleratorRuntime): AcceleratorObservation[] {
  return (arr(value) ?? []).flatMap((entry) => {
    const r = obj(entry);
    if (!r) return [];
    const index = count(r.index);
    const name = safeText(r.name) ?? `${runtime === "pytorch_xpu" ? "XPU" : "CUDA"} ${index ?? "?"}`;
    return [{ runtime, index, name, usable: r.usable === true }];
  });
}

const PROBE_STATUS: readonly ProbeStatus[] = ["completed", "timeout", "error"];

function devices(value: unknown): DeviceObservation | null {
  const r = obj(value);
  if (!r) return null;
  const cpu = obj(r.cpu);
  const ct2 = obj(r.ctranslate2);
  const torch = obj(r.pytorch);
  const openvino = obj(r.openvino);
  const native = obj(r.native);
  const variants = obj(native?.variants);
  return {
    cpu: { logicalCores: count(cpu?.logical_cores), physicalCores: count(cpu?.physical_cores) },
    ctranslate2Available: tri(ct2?.available),
    pytorchAvailable: tri(torch?.available),
    accelerators: capped([
      ...accelerators(ct2?.cuda_devices, "ctranslate2_cuda"),
      ...accelerators(torch?.cuda_devices, "pytorch_cuda"),
      ...accelerators(torch?.xpu_devices, "pytorch_xpu"),
    ]),
    openvino: openvino
      ? {
          available: tri(openvino.available),
          devices: capped((arr(openvino.values) ?? []).flatMap((v) => (identifier(v) ? [v as string] : []))),
        }
      : null,
    native: native
      ? {
          built: tri(native.built),
          variants: variants ? capped(Object.keys(variants).filter((k) => identifier(k) && variants[k] === true)) : [],
        }
      : null,
    probes: capped(
      (arr(r.probes) ?? []).flatMap((entry) => {
        const p = obj(entry);
        const name = identifier(p?.name);
        if (!p || name === null) return [];
        const status = (PROBE_STATUS as readonly unknown[]).includes(p.status) ? (p.status as ProbeStatus) : "unknown";
        return [{ name, status, cached: p.cached === true }];
      }),
    ),
    probeCacheHit: tri(r.probe_cache_hit),
  };
}

function installedModels(value: unknown): InstalledModel[] | null {
  const rows = arr(value);
  if (rows === null) return null;
  return rows.flatMap((entry) => {
    const r = obj(entry);
    if (!r || r.installed !== true) return [];
    const label = safeText(r.display_name) ?? safeText(r.model_id) ?? safeText(r.key);
    return [{ backend: identifier(r.backend) ?? "unknown", label: label ?? "?" }];
  });
}

function nativeRunnable(value: unknown): string[] | null {
  const runnable = obj(obj(value)?.runnable);
  if (!runnable) return null;
  return capped(Object.keys(runnable).filter((k) => identifier(k) && runnable[k] === true));
}

function openvinoIr(value: unknown): LegacyEnvironment["openvinoIr"] {
  const rows = arr(value);
  if (rows === null) return null;
  const valid = rows.map(obj).filter((r): r is Record<string, unknown> => r !== null);
  return { installed: valid.filter((r) => r.installed === true).length, total: valid.length };
}

/** `options.guidance` is server prose; only its known meanings are shown (in the UI language). */
function guidance(value: unknown): GuidanceCode[] {
  const codes = (arr(value) ?? []).flatMap((entry): GuidanceCode[] => {
    if (typeof entry !== "string") return [];
    if (entry.includes("未ビルド")) return ["native_not_built"];
    if (entry.includes("GGML")) return ["ggml_model_missing"];
    // "CLIのauto選択に従い…既定にしました" describes the legacy form's default; the new UI has no
    // such form in S2, so repeating it would be misleading.
    if (entry.includes("auto選択")) return [];
    return ["other"];
  });
  return [...new Set(codes)];
}

const PROBE_COMMANDS: readonly [string, ProbeId][] = [
  ["profiles list", "profiles"],
  ["devices", "devices"],
  ["models list-openvino", "openvino_models"],
  ["models list", "models"],
  ["models path", "model_path"],
  ["native status", "native"],
];

/**
 * Map one masked legacy error string to a fixed code. The text itself is never kept: it may
 * carry machine paths or stderr. Only the shapes produced by `CliAdapter` are recognised.
 */
export function classifyIssue(message: unknown): EnvironmentIssue {
  if (typeof message !== "string") return { kind: "malformed", probe: null, seconds: null };
  const timeout = /^CLI timed out after (\d{1,5}(?:\.\d+)?)s: (.*)$/s.exec(message);
  if (timeout) {
    const args = timeout[2] ?? "";
    const probe = PROBE_COMMANDS.find(([prefix]) => args === prefix || args.startsWith(`${prefix} `))?.[1] ?? null;
    return { kind: "timeout", probe, seconds: Math.round(Number(timeout[1])) };
  }
  if (/^Profile (Python )?is not available/.test(message)) return { kind: "failed", probe: "profile_runtime", seconds: null };
  return { kind: "failed", probe: null, seconds: null };
}

export function parseEnvironment(raw: unknown): LegacyEnvironment {
  const r = record(raw, "environment");
  const options = record(r.options, "environment.options");
  const profiles = list(r.profiles, "profiles").map((p, i): LegacyProfile => {
    const row = record(p, `profiles[${i}]`);
    return {
      name: str(row.name, `profiles[${i}].name`),
      exists: bool(row.exists, `profiles[${i}].exists`),
      compatible: tri(row.compatible),
      compatibilityReason: reason(row.compatibility_reason),
    };
  });
  const active = r.active_profile;
  if (active !== null && typeof active !== "string") throw new ShapeError("active_profile");
  // The server only ever reports a profile that exists; anything else is not shown as in use.
  if (active !== null && !profiles.some((p) => p.name === active && p.exists)) throw new ShapeError("active_profile");
  const issues = list(r.errors, "errors").map(classifyIssue);
  const parsedOptions = (key: "asr" | "diarization") => {
    const entries = list(options[key], `options.${key}`);
    const parsed = entries.map(option);
    if (parsed.some((o) => o === null)) issues.push({ kind: "malformed", probe: "options", seconds: null });
    return capped(parsed.filter((o): o is LegacyOption => o !== null));
  };
  const asr = parsedOptions("asr");
  const diarization = parsedOptions("diarization");
  // Without an active profile the server does not query models (it returns `[]`): not obtained.
  const models = active === null ? null : installedModels(r.models);
  return {
    profiles,
    activeProfile: active,
    asr,
    diarization,
    languageCount: arr(options.languages)?.length ?? 0,
    devices: devices(r.devices),
    installedModels: models === null ? null : capped(models),
    installedModelCount: models === null ? null : models.length,
    nativeRunnable: nativeRunnable(r.native),
    openvinoIr: openvinoIr(r.openvino_models),
    guidance: guidance(options.guidance),
    issues: capped(issues),
    profileWarningCount: arr(r.profile_warnings)?.length ?? 0,
  };
}
