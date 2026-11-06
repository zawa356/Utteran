<script setup lang="ts">
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { allPresets, effectiveSettings, type InspectorTab } from "../domain/demo";
import { presetDiff } from "../domain/presets";
import type { SettingKey } from "../domain/settingsSchema";
import type { PreviewKey } from "../i18n";
import { usePresetName } from "./usePresetName";

/**
 * Apply a preset to the Inspector's current target. Before "Apply", the row says exactly what
 * will change: "n items of the common defaults" or "n items set individually on the m selected
 * files" (and which). Managing presets (new / import / export) is on the Settings page.
 */
const props = defineProps<{ target: InspectorTab }>();
const { demo, state, t } = usePreview();
const { presetName } = usePresetName();
const chosen = ref<string | null>(null);
const items = computed(() => allPresets(state.value).map((p) => ({ value: p.id, title: presetName(p) })));
const preset = computed(() => allPresets(state.value).find((p) => p.id === chosen.value) ?? null);
const changed = computed<SettingKey[]>(() => {
  if (!preset.value) return [];
  const bases = props.target === "common" ? [state.value.common] : state.value.selected.map((id) => effectiveSettings(state.value, id));
  const keys = new Set<SettingKey>();
  for (const b of bases) for (const k of presetDiff(b, preset.value)) keys.add(k);
  return [...keys];
});
const names = computed(() => changed.value.map((k) => t(`field.${k}` as PreviewKey)).join(t("list.separator")));
const preview = computed(() => {
  if (!preset.value) return "";
  if (changed.value.length === 0) return t("presetApply.same");
  return props.target === "common"
    ? t("presetApply.common", { n: changed.value.length, names: names.value })
    : t("presetApply.selection", { n: changed.value.length, count: state.value.selected.length, names: names.value });
});
function apply() {
  if (!chosen.value) return;
  demo.applyPreset(chosen.value, props.target);
  chosen.value = null;
}
</script>

<template>
  <div class="p-presetapply" data-testid="preset-apply">
    <div class="p-presetapply__row">
      <v-select v-model="chosen" :items="items" :label="t('presetApply.label')" clearable hide-details data-testid="preset-apply-select" />
      <v-btn variant="tonal" color="primary" :disabled="!preset || changed.length === 0" data-testid="preset-apply-button" @click="apply">{{ t("presetApply.apply") }}</v-btn>
    </div>
    <p v-if="preview" class="text-body-medium" role="status" data-testid="preset-apply-preview">{{ preview }}</p>
  </div>
</template>
