import { usePreview } from "../composables";
import type { ProcessingSettings } from "../domain/fixtures";

/** "Balanced (example) / Japanese / diarization (auto) / SRT · JSON" for a frozen snapshot. */
export function useSnapshotText() {
  const { t } = usePreview();
  const snapshotText = (s: ProcessingSettings) =>
    [
      t(`model.${s.model}`),
      t(`lang.${s.language}`),
      s.diarization ? t("summary.diarizationOn", { speakers: s.speakers === "auto" ? t("speakers.auto") : t("speakers.n", { n: s.speakers }) }) : t("summary.diarizationOff"),
      s.formats.map((f) => f.toUpperCase()).join(" · "),
    ].join(" / ");
  return { snapshotText };
}
