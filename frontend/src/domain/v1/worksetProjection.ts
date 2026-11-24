/**
 * E-B: the explicit, serializable projection of the confirmed editing state (never a dump of the
 * runtime stores). Pure functions only.
 *
 * Saved: the ordered input rows (path, source, selection, output choice, applied overrides incl.
 * an explicit — possibly empty — glossary), the primary row, the frozen Preset/Policy id+revision
 * and the common differences (language/formats: null = inherit) + the common output folder
 * (one that only follows the saved default is saved as empty = "follow the default").
 *
 * Not saved (by construction, nothing here reads it): the typed-path field, staged bulk edits,
 * glossary drafts, Preset dialogs, validation/resolution results and tokens, idempotency intents,
 * admission records, job/queue/SSE state, row ids (page-local) and file ids.
 */
import type { InputWire, OverridesWire, WorksetStateWire } from "../../api/worksets";
import type { OutputFormatV1, Ref } from "../../api/v1Resources";
import type { CollectionState, InputRow, SettingOverrides } from "../inputCollection";
import type { FormState } from "../transcription";
import type { LibraryState } from "./library";
import { canonical } from "./settings";
import type { ScreenDifferences } from "./settings";

export function emptyWorksetState(): WorksetStateWire {
  return {
    schema_version: 1,
    preset_ref: null,
    policy_ref: null,
    common: { language: null, formats: null, output_dir: "", output_source: null },
    inputs: [],
    primary_index: null,
    glossaries: [],
  };
}

function overridesWire(o: Readonly<SettingOverrides>, glossaryIndex: (terms: readonly string[]) => number): OverridesWire {
  const out: OverridesWire = {};
  if (o.asrBackend !== undefined) out.asr_backend = o.asrBackend;
  if (o.asrModel !== undefined) out.asr_model = o.asrModel;
  if (o.asrDevice !== undefined) out.asr_device = o.asrDevice;
  if (o.language !== undefined) out.language = o.language;
  if (o.formats !== undefined) out.formats = [...o.formats] as OutputFormatV1[];
  if (o.glossary !== undefined) out.glossary = glossaryIndex(o.glossary);
  return out;
}

function refOf(selection: LibraryState["preset"] | LibraryState["policy"]): Ref | null {
  return selection.ref ? { id: selection.ref.id, revision: selection.ref.revision } : null;
}

/** The state a workset saves for the screen as it is now. */
export function project(inputs: Pick<CollectionState, "rows" | "primaryId">, form: Pick<FormState, "draft">, library: Pick<LibraryState, "preset" | "policy" | "screen">): WorksetStateWire {
  const glossaries: string[][] = [];
  const seen = new Map<string, number>();
  // Canonical de-duplication: a glossary gets its index at first use, in row order.
  const glossaryIndex = (terms: readonly string[]) => {
    const key = JSON.stringify(terms);
    let index = seen.get(key);
    if (index === undefined) {
      index = glossaries.length;
      glossaries.push([...terms]);
      seen.set(key, index);
    }
    return index;
  };
  const rows: InputWire[] = inputs.rows.map((row) => ({
    input_path: row.inputPath,
    source: row.source,
    selected: row.selected,
    output: row.output.kind === "custom" ? { kind: "custom", path: row.output.path } : { kind: "inherit" },
    overrides: overridesWire(row.overrides, glossaryIndex),
  }));
  const primary = inputs.rows.findIndex((r) => r.clientFileId === inputs.primaryId);
  // An output that only follows the saved default is saved as "follow the default" (empty), so a
  // restored workset picks up the default as it is then, not a stale copy.
  const followsDefault = form.draft.outputSource === "saved_default";
  return {
    schema_version: 1,
    preset_ref: refOf(library.preset),
    policy_ref: refOf(library.policy),
    common: {
      language: library.screen.language,
      formats: library.screen.formats === null ? null : [...library.screen.formats],
      output_dir: followsDefault ? "" : form.draft.outputDir,
      output_source: followsDefault ? null : form.draft.outputSource,
    },
    inputs: rows,
    primary_index: rows.length === 0 ? null : Math.max(primary, 0),
    glossaries,
  };
}

export function stateKey(state: WorksetStateWire): string {
  return canonical(state);
}

/** UTF-8 size of the canonical state (the server's limit is on the same bytes). */
export function stateBytes(state: WorksetStateWire): number {
  return new TextEncoder().encode(stateKey(state)).length;
}

export interface RestoredRow {
  inputPath: string;
  source: InputRow["source"];
  selected: boolean;
  output: InputRow["output"];
  overrides: SettingOverrides;
}

export interface RestorePlan {
  rows: RestoredRow[];
  primaryIndex: number | null;
  outputDir: string;
  outputSource: FormState["draft"]["outputSource"];
  screen: ScreenDifferences;
  presetRef: Ref | null;
  policyRef: Ref | null;
}

/** The inverse of `project` (row ids are new page-local ids, assigned by the input list). */
export function restorePlan(state: WorksetStateWire): RestorePlan {
  return {
    rows: state.inputs.map((row) => {
      const o = row.overrides;
      const overrides: SettingOverrides = {};
      if (o.asr_backend !== undefined) overrides.asrBackend = o.asr_backend;
      if (o.asr_model !== undefined) overrides.asrModel = o.asr_model;
      if (o.asr_device !== undefined) overrides.asrDevice = o.asr_device;
      if (o.language !== undefined) overrides.language = o.language as SettingOverrides["language"];
      if (o.formats !== undefined) overrides.formats = Object.freeze([...o.formats]) as SettingOverrides["formats"];
      if (o.glossary !== undefined) overrides.glossary = Object.freeze([...state.glossaries[o.glossary]!]);
      return {
        inputPath: row.input_path,
        source: row.source,
        selected: row.selected,
        output: row.output.kind === "custom" ? { kind: "custom" as const, path: row.output.path } : { kind: "inherit" as const },
        overrides,
      };
    }),
    primaryIndex: state.primary_index,
    outputDir: state.common.output_dir,
    outputSource: state.common.output_source,
    screen: { language: state.common.language, formats: state.common.formats === null ? null : [...state.common.formats] },
    presetRef: state.preset_ref,
    policyRef: state.policy_ref,
  };
}
