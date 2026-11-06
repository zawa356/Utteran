<script setup lang="ts">
import { ref, watch } from "vue";
import { usePreview } from "../composables";
import type { FieldDef, ProcessingSettings } from "../domain/settingsSchema";
import { useSettingAccess } from "./useSettingAccess";

/**
 * A number setting. What is typed stays in the field; a parseable number is applied at once (the
 * validator then reports a value out of range), text that is not a number is never applied and
 * shows its own message. Range checks are the same function the import uses.
 */
const props = defineProps<{ id: string; def: FieldDef; label: string; hint?: string; disabled: boolean }>();
const access = useSettingAccess();
const { t } = usePreview();
const stored = () => (access.mixed(props.def.key) ? "" : String(access.value(props.def.key)));
const draft = ref(stored());
const parseError = ref(false);
watch(stored, (v) => {
  if (!parseError.value) draft.value = v;
});
function onInput(text: string) {
  draft.value = text;
  const n = Number(text);
  if (text.trim() === "" || !Number.isFinite(n)) {
    parseError.value = true;
    return;
  }
  parseError.value = false;
  access.set(props.def.key, n as ProcessingSettings[typeof props.def.key]);
}
</script>

<template>
  <v-text-field
    :id="id"
    :model-value="draft"
    type="number"
    inputmode="decimal"
    :min="def.min"
    :max="def.max"
    :step="def.step"
    :placeholder="access.mixed(def.key) ? t('inspector.mixedShort') : undefined"
    :disabled="disabled"
    :hint="hint"
    :persistent-hint="hint !== undefined"
    hide-details="auto"
    :error-messages="parseError ? t('issue.notNumber') : access.issueText(def.key)"
    :aria-label="label"
    class="p-number"
    :data-testid="`input-${def.key}-${access.mode}`"
    @update:model-value="onInput"
  />
</template>
