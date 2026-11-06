import { usePreview } from "../composables";
import type { PresetDef } from "../domain/presets";
import { isPreviewKey } from "../i18n";

/** Display name of a preset: built-ins are translated, user presets keep their own name. */
export function usePresetName() {
  const { t } = usePreview();
  const presetName = (p: Pick<PresetDef, "name" | "nameKey">) => (p.nameKey && isPreviewKey(p.nameKey) ? t(p.nameKey) : p.name);
  return { presetName };
}
