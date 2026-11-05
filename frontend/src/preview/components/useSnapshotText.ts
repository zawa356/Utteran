import { usePreview } from "../composables";
import type { ProcessingSettings } from "../domain/settingsSchema";
import { isPreviewKey, type PreviewKey } from "../i18n";

/**
 * "Balanced / Japanese / diarization (auto) / SRT · JSON / prompt set" for a frozen snapshot.
 * The prompt and glossary are summarised as "set" / "n terms" — their text is never shown here,
 * in logs or in any export.
 */
export function useSnapshotText() {
  const { t } = usePreview();
  const label = (prefix: string, value: string) => (isPreviewKey(`${prefix}.${value}`) ? t(`${prefix}.${value}` as PreviewKey) : value);
  const speakers = (s: ProcessingSettings) =>
    s.speakerMode === "fixed" ? t("speakers.n", { n: s.numSpeakers }) : s.speakerMode === "range" ? t("speakers.range", { min: s.minSpeakers, max: s.maxSpeakers }) : t("speakers.auto");
  const snapshotText = (s: ProcessingSettings) =>
    [
      s.backend === "auto" ? label("model", s.model) : `${label("opt.backend", s.backend)} · ${s.model}`,
      t(`lang.${s.language}`),
      s.diarization ? t("summary.diarizationOn", { speakers: speakers(s) }) : t("summary.diarizationOff"),
      s.formats.map((f) => f.toUpperCase()).join(" · "),
      ...(s.initialPrompt ? [t("summary.prompt", { n: s.initialPrompt.length })] : []),
      ...(s.glossary.length > 0 ? [t("summary.glossary", { n: s.glossary.length })] : []),
    ].join(" / ");
  return { snapshotText, speakers };
}
