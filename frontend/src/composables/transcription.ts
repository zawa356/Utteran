import { computed } from "vue";
import type { FormIssue } from "../domain/transcription";
import { evaluate } from "../domain/transcription";
import { useCore } from "./core";
import { useStore } from "./useStore";

/** Shared read side of the S3-A form and job (Normal and Expert read the same controllers). */
export function useTranscription() {
  const core = useCore();
  const form = useStore(core.transcription.store);
  const env = useStore(core.environment.store);
  const job = useStore(core.job.store);
  const evaluation = computed(() => evaluate(form.value.draft, env.value));
  // `job.value` is read so the block follows every job state change.
  const block = computed(() => (job.value ? core.job.blockFor(evaluation.value.request) : null));
  const issue = (field: FormIssue["field"]) => evaluation.value.issues.find((i) => i.field === field) ?? null;
  return { core, form, env, job, evaluation, block, issue };
}
