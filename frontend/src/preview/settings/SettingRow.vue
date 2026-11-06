<script setup lang="ts">
import { computed } from "vue";
import InspectorField from "../components/InspectorField.vue";
import { usePreview } from "../composables";
import { type FieldDef, optionsFor, OUTPUT_FORMATS, type OutputFormat, type ProcessingSettings } from "../domain/settingsSchema";
import { isPreviewKey, type PreviewKey } from "../i18n";
import GlossaryEditor from "./GlossaryEditor.vue";
import NumberField from "./NumberField.vue";
import PromptField from "./PromptField.vue";
import SpeakersField from "./SpeakersField.vue";
import { useSettingAccess } from "./useSettingAccess";

/**
 * One settings row: label, where the value comes from (common / individual / mixed) and the
 * control the schema names. The same row serves the Expert Inspector and Normal mode.
 */
const props = defineProps<{ def: FieldDef; simple?: boolean }>();
const access = useSettingAccess();
const { t } = usePreview();
const key = computed(() => props.def.key);
const label = computed(() => t(`field.${key.value}` as PreviewKey));
const hintKey = computed(() => `hint.${key.value}`);
const disabledByRule = computed(() => access.disabled(props.def));
const hint = computed(() => {
  if (disabledByRule.value && isPreviewKey(`disabledHint.${key.value}`)) return t(`disabledHint.${key.value}` as PreviewKey);
  return isPreviewKey(hintKey.value) ? t(hintKey.value as PreviewKey) : undefined;
});
const items = computed(() => optionsFor(key.value, access.targets.value[0]!).map((v) => ({ value: v, title: access.optionLabel(key.value, v) })));
const id = computed(() => `p-field-${access.mode}-${key.value}`);
function setFormats(v: unknown) {
  if (Array.isArray(v) && v.length > 0) access.set("formats", v as OutputFormat[]);
}
</script>

<template>
  <InspectorField :field="def.key" :label="label" :mode="access.mode" :label-for="def.kind === 'select' || def.kind === 'number' ? id : undefined">
    <template #default="{ disabled }">
      <v-select
        v-if="def.kind === 'select'"
        :id="id"
        :items="items"
        :model-value="access.mixed(def.key) ? null : access.value(def.key)"
        :placeholder="t('inspector.mixedShort')"
        :disabled="disabled || disabledByRule"
        :hint="hint"
        :persistent-hint="hint !== undefined"
        :aria-label="label"
        :data-testid="`input-${def.key}-${access.mode}`"
        @update:model-value="(v) => v !== null && access.set(def.key, v as ProcessingSettings[typeof def.key])"
      />
      <v-switch
        v-else-if="def.kind === 'switch'"
        :model-value="access.value(def.key)"
        :indeterminate="access.mixed(def.key)"
        :label="access.value(def.key) ? t('common.on') : t('common.off')"
        :disabled="disabled || disabledByRule"
        :hint="hint"
        :persistent-hint="hint !== undefined"
        :hide-details="hint === undefined"
        color="primary"
        :aria-label="label"
        :data-testid="`input-${def.key}-${access.mode}`"
        @update:model-value="(v) => access.set(def.key, Boolean(v) as ProcessingSettings[typeof def.key])"
      />
      <NumberField v-else-if="def.kind === 'number'" :id="id" :def="def" :label="label" :hint="hint" :disabled="disabled || disabledByRule" />
      <v-chip-group
        v-else-if="def.kind === 'formats'"
        :model-value="access.value('formats')"
        multiple
        column
        selected-class="text-primary"
        class="p-formats"
        :disabled="disabled"
        :aria-label="label"
        :data-testid="`input-formats-${access.mode}`"
        @update:model-value="setFormats"
      >
        <v-chip v-for="f in OUTPUT_FORMATS" :key="f" :value="f" filter label size="default" :disabled="disabled" variant="outlined">{{ f.toUpperCase() }}</v-chip>
      </v-chip-group>
      <PromptField v-else-if="def.kind === 'prompt'" :label="label" :disabled="disabled" />
      <GlossaryEditor v-else-if="def.kind === 'glossary'" :disabled="disabled" />
      <SpeakersField v-else-if="def.kind === 'speakers'" :disabled="disabled || disabledByRule" :simple="simple" :hint="hint" />
    </template>
  </InspectorField>
</template>
