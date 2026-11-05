import { usePreview } from "../composables";
import type { ProcessingSettings, SettingKey } from "../domain/settingsSchema";
import { isPreviewKey, type PreviewKey } from "../i18n";

/** Human-readable value of one setting (prompt and glossary only as "n characters / n terms"). */
export function useSettingText() {
  const { t } = usePreview();
  function valueText<K extends SettingKey>(key: K, value: ProcessingSettings[K] | undefined): string {
    if (value === undefined) return "—";
    if (key === "initialPrompt") return value ? t("summary.promptChars", { n: String(value).length }) : t("summary.none");
    if (key === "glossary") return t("summary.terms", { n: (value as string[]).length });
    if (key === "formats") return (value as string[]).map((f) => f.toUpperCase()).join(" · ");
    if (typeof value === "boolean") return value ? t("common.on") : t("common.off");
    const own = `opt.${key}.${String(value)}`;
    if (isPreviewKey(own)) return t(own);
    if (key === "language" && isPreviewKey(`lang.${String(value)}`)) return t(`lang.${String(value)}` as PreviewKey);
    if (key === "model" && isPreviewKey(`model.${String(value)}`)) return t(`model.${String(value)}` as PreviewKey);
    return String(value);
  }
  const fieldName = (key: SettingKey) => t(`field.${key}` as PreviewKey);
  return { valueText, fieldName };
}
