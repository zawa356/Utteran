import { usePreview } from "../composables";
import type { DemoJob } from "../domain/demo";
import type { ProcessingSettings } from "../domain/settingsSchema";
import { isPreviewKey, type PreviewKey } from "../i18n";

/**
 * Short labels that tell jobs of the same file apart (#33): job id, run number, model, and the
 * output folder. Never the prompt or glossary text.
 */
export function useJobText() {
  const { t } = usePreview();
  const model = (s: ProcessingSettings) => {
    const key = `model.${s.model}`;
    return s.backend === "auto" && isPreviewKey(key) ? t(key as PreviewKey) : s.model;
  };
  /** "demo-job-004 · large-v3" */
  const jobLine = (job: Pick<DemoJob, "id" | "snapshot">) => `${job.id} · ${model(job.snapshot)}`;
  const dir = (d: string | null) => d ?? t("output.sameAsInput");
  return { model, jobLine, dir };
}
