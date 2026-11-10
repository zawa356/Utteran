import { computed } from "vue";
import { evaluateRow, isLocked, type RowEvaluation } from "../domain/inputCollection";
import type { FormIssue } from "../domain/transcription";
import { evaluate } from "../domain/transcription";
import { useCore } from "./core";
import { useStore } from "./useStore";

/**
 * Shared read side of the transcription screen. Normal and Expert read the same controllers:
 * the common settings (`transcription`), the one input list (`inputs`), the start door (`gate`),
 * the single-job tracker (`job`) and the staged batch (`batch`).
 */
export function useTranscription() {
  const core = useCore();
  const form = useStore(core.transcription.store);
  const env = useStore(core.environment.store);
  const job = useStore(core.job.store);
  const list = useStore(core.inputs.store);
  const gate = useStore(core.gate.store);
  const batch = useStore(core.batch.store);
  const guarded = (path: string) => gate.value.records.length > 0 && core.gate.isGuarded(path);
  const primary = computed(() => list.value.rows.find((r) => r.clientFileId === list.value.primaryId) ?? null);
  /** The common settings alone (choices, profile, setting issues). */
  const common = computed(() => evaluate({ ...form.value.draft, inputPath: "", inputSource: null }, env.value));
  /** What the single start would send: the primary row (or an empty input when there is none). */
  const evaluation = computed<RowEvaluation>(() =>
    primary.value
      ? evaluateRow(primary.value, form.value.draft, env.value, guarded)
      : { ...evaluate({ ...form.value.draft, inputPath: "", inputSource: null }, env.value), sameInput: false },
  );
  const statusOf = (id: string) => {
    const row = list.value.rows.find((r) => r.clientFileId === id);
    // `gate.value` is read so the status follows every ledger change.
    return row && gate.value ? core.inputs.statusOf(row) : "not_submitted";
  };
  const primaryStatus = computed(() => (primary.value ? statusOf(primary.value.clientFileId) : "not_submitted"));
  const primaryLocked = computed(() => isLocked(primaryStatus.value));
  // `job`/`gate` are read so the block follows every job and door change.
  // A guarded row evaluates without a request, so its "same input" reason is added after the others.
  const block = computed(() => (job.value && gate.value ? (core.job.blockFor(evaluation.value.request) ?? (evaluation.value.sameInput ? "same_input" : null)) : null));
  const issue = (field: FormIssue["field"]) => evaluation.value.issues.find((i) => i.field === field) ?? null;
  const commonIssue = (field: FormIssue["field"]) => common.value.issues.find((i) => i.field === field) ?? null;
  return { core, form, env, job, list, gate, batch, primary, primaryStatus, primaryLocked, statusOf, common, evaluation, block, issue, commonIssue };
}
