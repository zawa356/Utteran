/**
 * E-B: typed client for `/api/v1/worksets` (`--next-ui-v1` only). Contract authority: code first,
 * `src/utteran_gui/worksets_api.py` / `worksets_store.py`, schema
 * `docs/architecture/ui3/e-b-worksets.schema.json`, spec `docs/architecture/ui3/e-b-worksets.md`.
 *
 * - Updates are `PUT` with a strong `If-Match` (CAS). This client never resends; after an unknown
 *   outcome the caller reconciles with `GET` (same content = saved, unchanged revision = not
 *   applied, anything else = conflict).
 * - Creation carries an Idempotency-Key (independent of B5's replay store); the active pointer is
 *   an absolute value (safe to resend).
 * - The private state is kept only in memory and in authenticated bodies (never in storage/URL).
 */
import { bool, int, list, oneOf, record, ShapeError, str } from "./guards";
import type { HttpClient } from "./http";
import { IDEMPOTENCY_KEY_PATTERN, type MutationIntent } from "./idempotency";
import { ifMatch } from "./v1";
import { OUTPUT_FORMATS_V1, type OutputFormatV1, type Ref } from "./v1Resources";

export const WORKSET_TIMEOUT_MS = 30_000;
const WORKSET_ID = /^ws_[0-9a-f]{32}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T[0-9:.+\-Z]{1,40}$/;

export type ViewMode = "normal" | "expert";
export type InputSourceWire = "picker" | "drop" | "typed";
export type OutputSourceWire = "saved_default" | "picker" | "typed";

/** Wire overrides: an absent key inherits; `glossary` indexes `WorksetStateWire.glossaries`. */
export interface OverridesWire {
  asr_backend?: string;
  asr_model?: string;
  asr_device?: string;
  language?: string;
  formats?: OutputFormatV1[];
  glossary?: number;
}

export interface InputWire {
  input_path: string;
  source: InputSourceWire;
  selected: boolean;
  output: { kind: "inherit" } | { kind: "custom"; path: string };
  overrides: OverridesWire;
}

export interface WorksetStateWire {
  schema_version: 1;
  preset_ref: Ref | null;
  policy_ref: Ref | null;
  common: { language: string | null; formats: OutputFormatV1[] | null; output_dir: string; output_source: OutputSourceWire | null };
  inputs: InputWire[];
  primary_index: number | null;
  glossaries: string[][];
}

export interface WorksetSummary {
  id: string;
  name: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  stateBytes: number;
  inputCount: number;
}

export interface WorksetDoc extends WorksetSummary {
  state: WorksetStateWire;
}

export interface ActiveWorkset {
  worksetId: string;
  mode: ViewMode;
  revision: number;
}

export interface WorksetLimits {
  maxWorksets: number;
  maxInputs: number;
  maxStateBytes: number;
  maxStoreStateBytes: number;
  recoveryGenerations: number;
}

export interface WorksetListing {
  exists: boolean;
  items: WorksetSummary[];
  active: ActiveWorkset | null;
  limits: WorksetLimits;
}

export interface RecoveryItem {
  revision: number;
  name: string;
  savedAt: string;
  stateBytes: number;
  inputCount: number;
}

export type InputAvailability = "file" | "missing" | "not_file" | "unchecked";

function version(r: Record<string, unknown>, where: string): void {
  if (r.schema_version !== 1) throw new ShapeError(`${where}.schema_version`);
}
function worksetId(value: unknown, where: string): string {
  const text = str(value, where);
  if (!WORKSET_ID.test(text)) throw new ShapeError(where);
  return text;
}
function positive(value: unknown, where: string): number {
  const n = int(value, where);
  if (n < 1) throw new ShapeError(where);
  return n;
}
function time(value: unknown, where: string): string {
  const text = str(value, where);
  if (!TIMESTAMP.test(text) || Number.isNaN(Date.parse(text))) throw new ShapeError(where);
  return text;
}
function nullable<T>(value: unknown, parse: (v: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function formats(value: unknown, where: string): OutputFormatV1[] {
  return list(value, where).map((f, i) => oneOf(f, OUTPUT_FORMATS_V1, `${where}[${i}]`));
}
function refOf(value: unknown, where: string): Ref {
  const r = record(value, where);
  return { id: str(r.id, `${where}.id`), revision: positive(r.revision, `${where}.revision`) };
}

/** Shape check of a stored state (the server validated the full contract when it was saved). */
export function parseState(raw: unknown): WorksetStateWire {
  const r = record(raw, "state");
  version(r, "state");
  const common = record(r.common, "state.common");
  const inputs = list(r.inputs, "state.inputs").map((item, i): InputWire => {
    const where = `state.inputs[${i}]`;
    const row = record(item, where);
    const output = record(row.output, `${where}.output`);
    const kind = oneOf(output.kind, ["inherit", "custom"] as const, `${where}.output.kind`);
    const o = record(row.overrides, `${where}.overrides`);
    const overrides: OverridesWire = {};
    for (const key of ["asr_backend", "asr_model", "asr_device", "language"] as const) {
      if (key in o) overrides[key] = str(o[key], `${where}.overrides.${key}`);
    }
    if ("formats" in o) overrides.formats = formats(o.formats, `${where}.overrides.formats`);
    if ("glossary" in o) overrides.glossary = int(o.glossary, `${where}.overrides.glossary`);
    return {
      input_path: str(row.input_path, `${where}.input_path`),
      source: oneOf(row.source, ["picker", "drop", "typed"] as const, `${where}.source`),
      selected: bool(row.selected, `${where}.selected`),
      output: kind === "custom" ? { kind, path: str(output.path, `${where}.output.path`) } : { kind },
      overrides,
    };
  });
  const glossaries = list(r.glossaries, "state.glossaries").map((g, i) => list(g, `state.glossaries[${i}]`).map((t) => str(t, "state.glossaries[]")));
  const primary = nullable(r.primary_index, (v) => int(v, "state.primary_index"));
  if ((primary === null) !== (inputs.length === 0) || (primary !== null && primary >= inputs.length)) throw new ShapeError("state.primary_index");
  for (const row of inputs) if (row.overrides.glossary !== undefined && row.overrides.glossary >= glossaries.length) throw new ShapeError("state.glossary");
  return {
    schema_version: 1,
    preset_ref: nullable(r.preset_ref, (v) => refOf(v, "state.preset_ref")),
    policy_ref: nullable(r.policy_ref, (v) => refOf(v, "state.policy_ref")),
    common: {
      language: nullable(common.language, (v) => str(v, "state.common.language")),
      formats: nullable(common.formats, (v) => formats(v, "state.common.formats")),
      output_dir: str(common.output_dir, "state.common.output_dir"),
      output_source: nullable(common.output_source, (v) => oneOf(v, ["saved_default", "picker", "typed"] as const, "state.common.output_source")),
    },
    inputs,
    primary_index: primary,
    glossaries,
  };
}

function parseSummary(raw: unknown, where = "workset"): WorksetSummary {
  const r = record(raw, where);
  return {
    id: worksetId(r.id, `${where}.id`),
    name: str(r.name, `${where}.name`),
    revision: positive(r.revision, `${where}.revision`),
    createdAt: time(r.created_at, `${where}.created_at`),
    updatedAt: time(r.updated_at, `${where}.updated_at`),
    stateBytes: int(r.state_bytes, `${where}.state_bytes`),
    inputCount: int(r.input_count, `${where}.input_count`),
  };
}

export function parseDoc(raw: unknown): WorksetDoc {
  const r = record(raw, "workset");
  version(r, "workset");
  return { ...parseSummary(r), state: parseState(r.state) };
}

function parseActive(raw: unknown, where = "active"): ActiveWorkset {
  const r = record(raw, where);
  const view = record(r.view, `${where}.view`);
  return { worksetId: worksetId(r.workset_id, `${where}.workset_id`), mode: oneOf(view.mode, ["normal", "expert"] as const, `${where}.view.mode`), revision: positive(r.revision, `${where}.revision`) };
}

function parseActiveBody(raw: unknown): ActiveWorkset {
  const r = record(raw, "active");
  version(r, "active");
  return parseActive(r);
}

export function parseListing(raw: unknown): WorksetListing {
  const r = record(raw, "worksets");
  version(r, "worksets");
  const limits = record(r.limits, "worksets.limits");
  const items = list(r.items, "worksets.items").map((item, i) => parseSummary(item, `worksets.items[${i}]`));
  const active = nullable(r.active, (v) => parseActive(v));
  if (active && !items.some((i) => i.id === active.worksetId)) throw new ShapeError("worksets.active");
  return {
    exists: bool(r.exists, "worksets.exists"),
    items,
    active,
    limits: {
      maxWorksets: positive(limits.max_worksets, "limits.max_worksets"),
      maxInputs: positive(limits.max_inputs, "limits.max_inputs"),
      maxStateBytes: positive(limits.max_state_bytes, "limits.max_state_bytes"),
      maxStoreStateBytes: positive(limits.max_store_state_bytes, "limits.max_store_state_bytes"),
      recoveryGenerations: int(limits.recovery_generations, "limits.recovery_generations"),
    },
  };
}

function parseRecovery(raw: unknown): RecoveryItem[] {
  const r = record(raw, "recovery");
  version(r, "recovery");
  return list(r.items, "recovery.items").map((item, i) => {
    const x = record(item, `recovery.items[${i}]`);
    return {
      revision: positive(x.revision, "recovery.revision"),
      name: str(x.name, "recovery.name"),
      savedAt: time(x.saved_at, "recovery.saved_at"),
      stateBytes: int(x.state_bytes, "recovery.state_bytes"),
      inputCount: int(x.input_count, "recovery.input_count"),
    };
  });
}

export interface CreateBody {
  schema_version: 1;
  name: string;
  state?: WorksetStateWire;
}

export interface WorksetsApi {
  list(): Promise<WorksetListing>;
  /** First run only: creates the store and one default workset; otherwise returns the listing. */
  bootstrap(name: string): Promise<WorksetListing>;
  get(id: string, signal?: AbortSignal): Promise<WorksetDoc>;
  create(intent: MutationIntent<CreateBody>): Promise<WorksetDoc>;
  /** CAS replace (name + state); the answer's revision and ETag must agree. */
  put(id: string, expectedRevision: number, name: string, state: WorksetStateWire): Promise<WorksetDoc>;
  remove(id: string, expectedRevision: number): Promise<WorksetListing>;
  /** CAS on the pointer revision (`If-Match`, 0 = none yet): a late, older request answers 412. */
  setActive(id: string, mode: ViewMode, expectedRevision: number): Promise<ActiveWorkset>;
  getActive(): Promise<ActiveWorkset>;
  recovery(id: string): Promise<RecoveryItem[]>;
  restoreRecovery(id: string, revision: number, expectedRevision: number): Promise<WorksetDoc>;
  /** Read-only existence check of local inputs (same order; no path in the answer). */
  inputStatus(paths: readonly string[]): Promise<InputAvailability[]>;
}

function checkId(id: string): string {
  if (!WORKSET_ID.test(id)) throw new Error("workset id");
  return id;
}

export function createWorksetsApi(http: HttpClient): WorksetsApi {
  const docWithEtag = async (request: Parameters<HttpClient["request"]>[0]): Promise<WorksetDoc> => {
    const response = await http.request(request, parseDoc);
    if (response.headers.get("ETag") !== ifMatch(response.data.revision)) throw new ShapeError("etag");
    return response.data;
  };
  return {
    async list() {
      return (await http.request({ method: "GET", path: "/api/v1/worksets" }, parseListing)).data;
    },
    async bootstrap(name) {
      return (await http.request({ method: "POST", path: "/api/v1/worksets/bootstrap", body: { schema_version: 1, name }, timeoutMs: WORKSET_TIMEOUT_MS }, parseListing)).data;
    },
    get(id, signal) {
      return docWithEtag({ method: "GET", path: `/api/v1/worksets/${checkId(id)}`, signal });
    },
    async create(intent) {
      if (!IDEMPOTENCY_KEY_PATTERN.test(intent.key)) throw new Error("idempotency key");
      intent.attempts += 1;
      intent.state = "sent";
      try {
        const doc = await docWithEtag({ method: "POST", path: "/api/v1/worksets", body: intent.body, headers: { "Idempotency-Key": intent.key }, timeoutMs: WORKSET_TIMEOUT_MS });
        intent.state = "accepted";
        return doc;
      } catch (error) {
        const e = error as { outcomeUnknown?: boolean; status?: number | null };
        intent.state = !e.outcomeUnknown && typeof e.status === "number" && e.status >= 400 && e.status < 500 ? "rejected" : "unknown";
        throw error;
      }
    },
    async put(id, expectedRevision, name, state) {
      const doc = await docWithEtag({
        method: "PUT", path: `/api/v1/worksets/${checkId(id)}`, body: { schema_version: 1, name, state },
        headers: { "If-Match": ifMatch(expectedRevision) }, timeoutMs: WORKSET_TIMEOUT_MS,
      });
      if (doc.id !== id || doc.revision < expectedRevision) throw new ShapeError("workset.revision");
      return doc;
    },
    async remove(id, expectedRevision) {
      return (await http.request({ method: "DELETE", path: `/api/v1/worksets/${checkId(id)}`, headers: { "If-Match": ifMatch(expectedRevision) }, timeoutMs: WORKSET_TIMEOUT_MS }, parseListing)).data;
    },
    async setActive(id, mode, expectedRevision) {
      return (await http.request({ method: "PUT", path: "/api/v1/worksets/active", body: { schema_version: 1, workset_id: checkId(id), view: { mode } }, headers: { "If-Match": ifMatch(expectedRevision) }, timeoutMs: WORKSET_TIMEOUT_MS }, parseActiveBody)).data;
    },
    async getActive() {
      return (await http.request({ method: "GET", path: "/api/v1/worksets/active" }, parseActiveBody)).data;
    },
    async recovery(id) {
      return (await http.request({ method: "GET", path: `/api/v1/worksets/${checkId(id)}/recovery` }, parseRecovery)).data;
    },
    restoreRecovery(id, revision, expectedRevision) {
      if (!Number.isSafeInteger(revision) || revision < 1) throw new Error("revision");
      return docWithEtag({ method: "POST", path: `/api/v1/worksets/${checkId(id)}/recovery/${revision}/restore`, headers: { "If-Match": ifMatch(expectedRevision) }, timeoutMs: WORKSET_TIMEOUT_MS });
    },
    async inputStatus(paths) {
      const parse = (raw: unknown) => {
        const r = record(raw, "input_status");
        version(r, "input_status");
        const items = list(r.items, "input_status.items").map((x, i) => oneOf(record(x, `items[${i}]`).state, ["file", "missing", "not_file", "unchecked"] as const, `items[${i}].state`));
        if (items.length !== paths.length) throw new ShapeError("input_status.items");
        return items;
      };
      return (await http.request({ method: "POST", path: "/api/v1/worksets/input-status", body: { schema_version: 1, paths: [...paths] }, timeoutMs: 10_000 }, parse)).data;
    },
  };
}
