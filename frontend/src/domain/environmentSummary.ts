/**
 * Pure read-model for the environment screens (UI-3 S2). It only re-arranges what the legacy
 * `GET /api/environment` reported. Nothing here infers that a configuration can run: there is
 * deliberately no "ready", "available" or "recommended" output (G-01 stays with the backend).
 */
import type { LegacyEnvironment, LegacyProfile } from "../api/legacy";
import { type EnvironmentState, isCurrentResult } from "./environment";

/** The four items of the legacy environment strip (hardware, profile, installed models, native). */
export interface EnvironmentSummary {
  hardware: { kind: "gpu"; names: string[]; more: number } | { kind: "cpu"; threads: number | null } | { kind: "not_obtained" };
  profile: { kind: "value"; name: string } | { kind: "none" } | { kind: "not_obtained" };
  models: { kind: "count"; count: number } | { kind: "not_obtained" } | { kind: "unknown" };
  native: { kind: "variants"; names: string[] } | { kind: "none" } | { kind: "unknown" } | { kind: "not_obtained" };
}

const SHOWN_GPUS = 2;

export function summarize(data: LegacyEnvironment | null): EnvironmentSummary {
  if (!data) {
    return { hardware: { kind: "not_obtained" }, profile: { kind: "not_obtained" }, models: { kind: "not_obtained" }, native: { kind: "not_obtained" } };
  }
  const devices = data.devices;
  let hardware: EnvironmentSummary["hardware"];
  if (!devices) hardware = { kind: "not_obtained" };
  else {
    // Same rule as the legacy strip: GPUs a runtime probe marked usable, else CPU threads.
    // Names are de-duplicated (one card is often seen by both CTranslate2 and PyTorch).
    const names = [...new Set(devices.accelerators.filter((a) => a.usable).map((a) => a.name))];
    hardware = names.length
      ? { kind: "gpu", names: names.slice(0, SHOWN_GPUS), more: Math.max(0, names.length - SHOWN_GPUS) }
      : { kind: "cpu", threads: devices.cpu.logicalCores };
  }
  const variants = devices?.native?.variants;
  return {
    hardware,
    profile: data.activeProfile ? { kind: "value", name: data.activeProfile } : { kind: "none" },
    models:
      data.installedModelsState === "counted" && data.installedModelCount !== null
        ? { kind: "count", count: data.installedModelCount }
        : { kind: data.installedModelsState === "not_obtained" ? "not_obtained" : "unknown" },
    native: !devices
      ? { kind: "not_obtained" }
      : !devices.native || variants === null || variants === undefined
        ? { kind: "unknown" }
        : variants.length
          ? { kind: "variants", names: variants }
          : { kind: "none" },
  };
}

/** Presence and compatibility are two separate facts; `compatible: null` is "not known", not OK. */
export type ProfileStatus = "missing" | "incompatible" | "compat_unknown" | "built";

export function profileStatus(row: LegacyProfile): ProfileStatus {
  if (!row.exists) return "missing";
  if (row.compatible === false) return "incompatible";
  if (row.compatible === null) return "compat_unknown";
  return "built";
}

/** Profiles that exist but need a rebuild (what the server's `profile_warnings` describe). */
export function rebuildNeeded(data: LegacyEnvironment): LegacyProfile[] {
  return data.profiles.filter((p) => p.exists && p.compatible === false);
}

/**
 * How the server's choice relates to what was asked. Only meaningful for a current result:
 * the server silently uses the only built profile when the requested one is not built.
 */
export type SelectionNote =
  | { kind: "fallback"; requested: string; active: string }
  | { kind: "requested_unavailable"; requested: string }
  | { kind: "server_choice"; active: string }
  | { kind: "choose" }
  | { kind: "none_usable" }
  | null;

export function selectionNote(state: EnvironmentState): SelectionNote {
  if (!isCurrentResult(state) || !state.data) return null;
  const requested = state.requestedProfile;
  const active = state.data.activeProfile;
  if (requested !== null) {
    if (active === null) return { kind: "requested_unavailable", requested };
    return active === requested ? null : { kind: "fallback", requested, active };
  }
  if (active !== null) return { kind: "server_choice", active };
  // With several built profiles and none specified, the server picks none (a choice is needed);
  // with none built, there is nothing to choose.
  return state.data.profiles.some((p) => p.exists) ? { kind: "choose" } : { kind: "none_usable" };
}

/**
 * Role chips for one profile row of the details. They describe the answer being shown:
 * for a previous result (pending, failed, cancelled or a changed selection) only the server's
 * profile *of that answer* is marked, as such; the current selection and the current saved
 * default are never attached to it. The default shown is the one saved when that request was sent.
 */
export type ProfileRole = "active" | "active_previous" | "requested" | "default_at_request";

export function profileRoles(name: string, state: EnvironmentState): ProfileRole[] {
  const data = state.data;
  if (!data) return [];
  const current = isCurrentResult(state);
  const roles: ProfileRole[] = [];
  if (name === data.activeProfile) roles.push(current ? "active" : "active_previous");
  if (current && state.dataFor) {
    // "Selected here" only for a manual choice; a request that followed the default is marked by
    // the default chip instead.
    if (!state.dataFor.followsDefault && name === state.dataFor.profile) roles.push("requested");
    if (name === state.dataFor.savedDefault) roles.push("default_at_request");
  }
  return roles;
}

/** Number of separate things worth a warning in the short (Normal) view. */
export function noticeCount(data: LegacyEnvironment): { issues: number; rebuild: number; guidance: number } {
  return {
    issues: data.issues.length,
    rebuild: Math.max(rebuildNeeded(data).length, data.profileWarningCount),
    guidance: data.guidance.length,
  };
}
