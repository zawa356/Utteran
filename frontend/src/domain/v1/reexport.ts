/**
 * E-C: re-export one finished job from its verified JSON (no ASR). One dialog at a time, one job.
 * Distinct from the D3-B2 rerun (a new ASR job) and from retry: no job, no queue, no model.
 *
 * Flow: `open(job)` → `GET reexport-source` → the form starts from the job's frozen output
 * settings (labels start empty: the JSON already carries the job's mapping, it is never applied
 * twice) → `submit()` sends ONE intent (Idempotency-Key, `If-Match` = job revision, the source
 * digest shown) → `done` with the server record, or `rejected` (fixed code), or `unknown`.
 *
 * Unknown outcome (PR #81 lesson): the form is frozen and the dialog cannot be closed. Only an
 * explicit `recheck()` resends the *same* intent (same key, same body); the server answers from
 * the record of that key. Equal values never stand in for the lost request. After a settled
 * answer, `again()` starts a new intent (a new key, a new directory).
 */
import type { ApiError } from "../../api/errors";
import { MutationIntent, type RandomSource } from "../../api/idempotency";
import type { ReexportApi, ReexportBody, ReexportRecord, ReexportSource, Newline } from "../../api/reexport";
import { OUTPUT_FORMATS_V1, type OutputFormatV1 } from "../../api/v1Resources";
import { Store } from "../store";

export type ReexportStep = "closed" | "loading" | "load_error" | "form" | "submitting" | "unknown" | "done" | "rejected";

export interface ReexportForm {
  formats: OutputFormatV1[];
  showSpeaker: boolean;
  srtBom: boolean;
  newline: Newline;
  /** Displayed label -> new label. Absent = unchanged. */
  labels: Record<string, string>;
  /** `null` = the job's output parent (default); otherwise an explicit absolute folder. */
  parent: string | null;
}

export interface ReexportState {
  step: ReexportStep;
  jobId: string | null;
  source: ReexportSource | null;
  form: ReexportForm | null;
  record: ReexportRecord | null;
  error: ApiError | null;
  history: ReexportRecord[];
}

const initial: ReexportState = { step: "closed", jobId: null, source: null, form: null, record: null, error: null, history: [] };

export type FormIssue = "formats" | "label" | "parent" | "bom";

export class V1ReexportController {
  readonly store = new Store<ReexportState>(initial);
  private intent: MutationIntent<ReexportBody> | null = null;
  private generation = 0;

  constructor(private readonly api: ReexportApi, private readonly options: { random?: RandomSource } = {}) {}

  /** A dialog that must stay open: the request is in flight or its outcome is unknown. */
  locked(): boolean {
    const step = this.store.get().step;
    return step === "submitting" || step === "unknown";
  }

  async open(jobId: string): Promise<void> {
    if (this.store.get().step !== "closed") return;
    const generation = ++this.generation;
    this.intent = null;
    this.store.set({ ...initial, step: "loading", jobId });
    await this.load(jobId, generation);
  }

  async reload(): Promise<void> {
    const { jobId, step } = this.store.get();
    if (!jobId || this.locked() || step === "loading") return;
    const generation = ++this.generation;
    this.intent = null;
    this.store.set({ ...initial, step: "loading", jobId });
    await this.load(jobId, generation);
  }

  private async load(jobId: string, generation: number): Promise<void> {
    try {
      const [source, history] = await Promise.all([this.api.getSource(jobId), this.api.list(jobId).catch(() => [])]);
      if (generation !== this.generation) return;
      const d = source.defaults;
      this.store.patch({
        step: "form",
        source,
        history,
        form: { formats: [...d.formats], showSpeaker: d.showSpeaker, srtBom: d.srtBom && d.formats.includes("srt"), newline: d.newline, labels: {}, parent: null },
      });
    } catch (error) {
      if (generation !== this.generation) return;
      this.store.patch({ step: "load_error", error: error as ApiError });
    }
  }

  close(): void {
    if (this.locked()) return;
    this.generation += 1;
    this.intent = null;
    this.store.set(initial);
  }

  edit(change: Partial<ReexportForm>): void {
    const state = this.store.get();
    if (state.step !== "form" && state.step !== "rejected") return;
    if (!state.form) return;
    const next = { ...state.form, ...change };
    next.formats = OUTPUT_FORMATS_V1.filter(f => next.formats.includes(f));
    if (!next.formats.includes("srt")) next.srtBom = false; // the BOM applies to SRT only
    // An edit after a refusal is a new request: drop the old intent (its key stays unused).
    this.intent = null;
    this.store.patch({ form: next, step: "form", error: null });
  }

  setLabel(label: string, value: string | null): void {
    const form = this.store.get().form;
    if (!form) return;
    const labels = { ...form.labels };
    // An empty field means "unchanged" (PR review P3-8): clearing it never hides a speaker.
    if (value === null || value === "" || value === label) delete labels[label];
    else labels[label] = value;
    this.edit({ labels });
  }

  issues(): FormIssue[] {
    const { form, source } = this.store.get();
    if (!form || !source) return ["formats"];
    const out: FormIssue[] = [];
    if (form.formats.length === 0) out.push("formats");
    if (form.srtBom && !form.formats.includes("srt")) out.push("bom");
    const max = source.limits.maxLabel;
    if (Object.values(form.labels).some(v => v.length > max || [...v].some(c => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127))) out.push("label");
    if (form.parent !== null && (form.parent.trim() === "" || !/^(\/|[A-Za-z]:[\\/])/.test(form.parent))) out.push("parent");
    return out;
  }

  canSubmit(): boolean {
    const s = this.store.get();
    return s.step === "form" && !!s.source?.available && this.issues().length === 0;
  }

  private body(): ReexportBody | null {
    const { form, source } = this.store.get();
    if (!form || !source?.source) return null;
    return {
      schema_version: 1,
      source_sha256: source.source.sha256,
      formats: [...form.formats],
      show_speaker: form.showSpeaker,
      srt_bom: form.srtBom,
      newline: form.newline,
      speaker_labels: { ...form.labels },
      output_parent: form.parent,
    };
  }

  async submit(): Promise<void> {
    if (!this.canSubmit()) return;
    const body = this.body();
    const source = this.store.get().source;
    if (!body || !source) return;
    this.intent = new MutationIntent(Object.freeze(body) as ReexportBody, this.options.random);
    await this.send(source.jobId, source.jobRevision);
  }

  /** Unknown outcome only: the same key and the same body, once, on an explicit action. */
  async recheck(): Promise<void> {
    const source = this.store.get().source;
    if (this.store.get().step !== "unknown" || !this.intent || !source) return;
    await this.send(source.jobId, source.jobRevision);
  }

  private async send(jobId: string, revision: number): Promise<void> {
    const intent = this.intent;
    if (!intent) return;
    const generation = this.generation;
    this.store.patch({ step: "submitting", error: null });
    try {
      const { record } = await this.api.create(jobId, revision, intent);
      if (generation !== this.generation) return;
      const history = [record, ...this.store.get().history.filter(item => item.id !== record.id)];
      this.store.patch({ step: "done", record, history });
    } catch (error) {
      if (generation !== this.generation) return;
      this.store.patch({ step: intent.state === "unknown" ? "unknown" : "rejected", error: error as ApiError });
    }
  }

  /** A record still `pending` (another window is writing it): read it again by id. */
  async refreshRecord(): Promise<void> {
    const record = this.store.get().record;
    if (this.store.get().step !== "done" || !record) return;
    const generation = this.generation;
    try {
      const fresh = await this.api.get(record.id);
      if (generation !== this.generation) return;
      this.store.patch({ record: fresh, history: this.store.get().history.map(item => (item.id === fresh.id ? fresh : item)) });
    } catch {
      // Keep the shown record; the next read can try again.
    }
  }

  /** After a settled answer: a fresh form (new intent on the next submit, never a replay). */
  again(): void {
    const s = this.store.get();
    if (s.step !== "done" || !s.source) return;
    this.intent = null;
    this.store.patch({ step: "form", record: null, error: null });
  }

  dispose(): void {
    this.generation += 1;
  }
}
