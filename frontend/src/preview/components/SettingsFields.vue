<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { effectiveSettings, selectionField } from "../domain/demo";
import { DEMO_MODELS, LANGUAGES, OUTPUT_FORMATS, type ProcessingSettings, SPEAKER_COUNTS, type SettingKey } from "../domain/fixtures";
import InspectorField from "./InspectorField.vue";

/**
 * The processing form shared by the Expert Inspector (common defaults / selected inputs) and Normal
 * mode. "normal" shows the effective values of one input and edits what that input will run with.
 */
const props = withDefaults(defineProps<{ mode: "common" | "selection" | "normal"; inputId?: string | null; fields?: SettingKey[]; compact?: boolean }>(), {
  inputId: null,
  fields: () => ["model", "language", "diarization", "speakers", "formats"],
  compact: false,
});
const { demo, state, t } = usePreview();

function value<K extends SettingKey>(key: K): ProcessingSettings[K] {
  if (props.mode === "normal") return props.inputId ? effectiveSettings(state.value, props.inputId)[key] : state.value.common[key];
  return props.mode === "common" ? state.value.common[key] : selectionField(state.value, key).value;
}
function mixed(key: SettingKey): boolean {
  return props.mode === "selection" && selectionField(state.value, key).mixed;
}
function set<K extends SettingKey>(key: K, v: ProcessingSettings[K]) {
  if (props.mode === "normal") demo.setNormalSetting(props.inputId, key, v);
  else if (props.mode === "common") demo.setCommon(key, v);
  else demo.setOverride(key, v);
}
const models = computed(() => DEMO_MODELS.map((m) => ({ value: m, title: t(`model.${m}`) })));
const languages = computed(() => LANGUAGES.map((l) => ({ value: l, title: t(`lang.${l}`) })));
const speakers = computed(() => SPEAKER_COUNTS.map((n) => ({ value: n, title: n === "auto" ? t("speakers.auto") : t("speakers.n", { n }) })));
const show = (key: SettingKey) => props.fields.includes(key);
</script>

<template>
  <div :class="compact ? 'p-fields--compact' : 'u-stack'" :data-testid="`settings-fields-${mode}`">
    <InspectorField v-if="show('model')" field="model" :label="t('field.model')" :mode="mode">
      <template #default="{ disabled }">
        <v-select
          :items="models"
          :model-value="mixed('model') ? null : value('model')"
          :placeholder="t('inspector.mixedShort')"
          :disabled="disabled"
          :aria-label="t('field.model')"
          :data-testid="`input-model-${mode}`"
          @update:model-value="(v) => v && set('model', v)"
        />
      </template>
    </InspectorField>
    <InspectorField v-if="show('language')" field="language" :label="t('field.language')" :mode="mode">
      <template #default="{ disabled }">
        <v-select
          :items="languages"
          :model-value="mixed('language') ? null : value('language')"
          :placeholder="t('inspector.mixedShort')"
          :disabled="disabled"
          :aria-label="t('field.language')"
          :data-testid="`input-language-${mode}`"
          @update:model-value="(v) => v && set('language', v)"
        />
      </template>
    </InspectorField>
    <InspectorField v-if="show('diarization')" field="diarization" :label="t('field.diarization')" :mode="mode">
      <template #default="{ disabled }">
        <v-switch
          :model-value="value('diarization')"
          :indeterminate="mixed('diarization')"
          :label="value('diarization') ? t('common.on') : t('common.off')"
          :disabled="disabled"
          color="primary"
          hide-details
          :data-testid="`input-diarization-${mode}`"
          @update:model-value="(v) => set('diarization', Boolean(v))"
        />
      </template>
    </InspectorField>
    <InspectorField v-if="show('speakers')" field="speakers" :label="t('field.speakers')" :mode="mode">
      <template #default="{ disabled }">
        <v-select
          :items="speakers"
          :model-value="mixed('speakers') ? null : value('speakers')"
          :placeholder="t('inspector.mixedShort')"
          :disabled="disabled || !value('diarization')"
          :aria-label="t('field.speakers')"
          :hint="value('diarization') ? undefined : t('field.speakersNeedsDiarization')"
          persistent-hint
          :data-testid="`input-speakers-${mode}`"
          @update:model-value="(v) => v && set('speakers', v)"
        />
      </template>
    </InspectorField>
    <InspectorField v-if="show('formats')" field="formats" :label="t('field.formats')" :mode="mode">
      <template #default="{ disabled }">
        <v-chip-group
          :model-value="value('formats')"
          multiple
          column
          selected-class="text-primary"
          :disabled="disabled"
          :aria-label="t('field.formats')"
          :data-testid="`input-formats-${mode}`"
          @update:model-value="(v: unknown) => Array.isArray(v) && v.length > 0 && set('formats', v as ProcessingSettings['formats'])"
        >
          <v-chip v-for="f in OUTPUT_FORMATS" :key="f" :value="f" filter label size="default" :disabled="disabled" variant="outlined">{{ f.toUpperCase() }}</v-chip>
        </v-chip-group>
      </template>
    </InspectorField>
  </div>
</template>
