<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { usePreview } from "../composables";
import { PROMPT_MAX } from "../domain/settingsSchema";
import { useSettingAccess } from "./useSettingAccess";

/**
 * `initial_prompt` (multi-line). Japanese is typed through an IME: while a composition is open the
 * text is NOT applied (it would re-render under the IME); it is applied on compositionend. The
 * prompt is never shown in logs, job details or exports (only "set, n characters").
 */
defineProps<{ label: string; disabled: boolean }>();
const access = useSettingAccess();
const { t } = usePreview();
const stored = () => (access.mixed("initialPrompt") ? "" : access.value("initialPrompt"));
const draft = ref(stored());
const composing = ref(false);
watch(stored, (v) => {
  if (!composing.value) draft.value = v;
});
function commit() {
  if (draft.value !== access.value("initialPrompt")) access.set("initialPrompt", draft.value);
}
function onInput(v: string) {
  draft.value = v;
  if (!composing.value) commit();
}
function onCompositionEnd() {
  composing.value = false;
  commit();
}
const counter = computed(() => t("prompt.count", { n: draft.value.length, max: PROMPT_MAX }));
</script>

<template>
  <div @compositionstart="composing = true" @compositionend="onCompositionEnd">
    <v-textarea
      :model-value="draft"
      :placeholder="access.mixed('initialPrompt') ? t('inspector.mixedShort') : t('prompt.placeholder')"
      :disabled="disabled"
      rows="3"
      auto-grow
      max-rows="10"
      :hint="counter"
      persistent-hint
      :error-messages="access.issueText('initialPrompt')"
      :aria-label="label"
      variant="outlined"
      density="compact"
      data-testid="input-initialPrompt"
      @update:model-value="onInput"
    />
  </div>
</template>
