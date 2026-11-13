import { computed } from "vue";
import type { ApiIssue } from "../api/errors";
import type { V1Core } from "../domain/core";
import type { V1Issue } from "../domain/v1/singleJob";
import { type MessageKey, translate } from "../i18n";
import { ja } from "../i18n/ja";
import { useCore, useI18n } from "./core";
import { useStore } from "./useStore";

/**
 * Read side of the S5-A v1 screen (`--next-ui-v1` only). Normal and Expert use the same
 * controllers (`core.v1.library`, `core.v1.job`): there is one start path in v1 mode.
 */
export function useV1() {
  const core = useCore();
  if (!core.v1) throw new Error("v1 core is not active");
  const v1: V1Core = core.v1;
  const library = useStore(v1.library.store);
  const job = useStore(v1.job.store);
  // Reading these stores makes `evaluation` follow every input, folder and selection change.
  const inputs = useStore(core.inputs.store);
  const form = useStore(core.transcription.store);
  const evaluation = computed(() => {
    void [library.value, inputs.value, form.value];
    return v1.job.evaluate();
  });
  const block = computed(() => {
    void job.value;
    return v1.job.blockFor();
  });
  const stale = computed(() => {
    void [job.value, evaluation.value];
    return v1.job.isStale();
  });
  return { core, v1, library, job, inputs, form, evaluation, block, stale };
}

/** Fixed codes → text; unknown codes get a safe generic sentence that keeps the code visible. */
export function useCodeText() {
  const { language, t } = useI18n();
  const known = (prefix: string, code: string): MessageKey | null => {
    const key = `${prefix}.${code}`;
    return key in ja ? (key as MessageKey) : null;
  };
  const reason = (code: string) => {
    const key = known("v1.reason", code);
    return key ? t(key) : translate(language.value, "v1.reason.other", { code });
  };
  const issue = (item: ApiIssue | { path: string; code: string }) => `${reason(item.code)}${item.path ? ` (${item.path})` : ""}`;
  const status = (code: string) => {
    const key = known("v1.status", code);
    return key ? t(key) : translate(language.value, "v1.status.other", { code });
  };
  const formIssue = (i: V1Issue) => {
    const field = t(`v1.field.${i.field}` as MessageKey);
    const problem =
      i.field === "input" && i.problem === "none"
        ? t("v1.problem.noInput")
        : i.field === "input" || i.field === "output"
          ? t(`tx.path.${i.problem}` as MessageKey)
          : t(`v1.problem.${i.problem}` as MessageKey);
    return `${field}: ${problem}`;
  };
  return { reason, issue, status, formIssue, t };
}
