import { usePreview } from "../composables";
import { modelName, setupModel } from "../domain/setupCatalog";
import type { PreviewKey } from "../i18n";

/** Sizes and model labels shared by the wizard pages. */
export function useSetupText() {
  const { t } = usePreview();
  const size = (mb: number | null) => (mb === null ? t("size.unknown") : mb >= 1000 ? t("size.gb", { n: (mb / 1000).toFixed(1) }) : t("size.mb", { n: mb }));
  const note = (id: string) => {
    const n = setupModel(id)?.note;
    return n ? t(`setupModel.note.${n}` as PreviewKey) : "";
  };
  return { size, note, modelName };
}
