<script setup lang="ts">
import { mdiAlertOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import SaveMock from "../dialogs/SaveMock.vue";
import { exportPreset, hasPrivateText, type PresetDef } from "../domain/presets";
import { usePresetName } from "./usePresetName";
import { useSettingText } from "./useSettingText";

/**
 * Export one preset as a versioned JSON document (a design CANDIDATE, not a published format).
 * The device is never written; prompt and glossary only when the user ticks the box, with their
 * presence stated next to it. The JSON is previewed; "Save…" writes nothing in the prototype.
 */
const props = defineProps<{ preset: PresetDef }>();
const emit = defineEmits<{ close: [] }>();
const { t } = usePreview();
const { presetName } = usePresetName();
const { fieldName } = useSettingText();
const includeText = ref(false);
const result = computed(() => exportPreset(props.preset, presetName(props.preset), { includePrivateText: includeText.value }));
const privateText = computed(() => hasPrivateText(props.preset));
</script>

<template>
  <v-dialog :model-value="true" max-width="640" scrollable @update:model-value="(v) => !v && emit('close')">
    <v-card variant="elevated" :title="t('preset.exportTitle')" data-testid="preset-export-dialog">
      <v-card-text class="u-stack">
        <template v-if="privateText">
          <v-checkbox v-model="includeText" :label="t('export.includeText')" color="primary" hide-details data-testid="export-include-text" />
          <p v-if="includeText" class="p-feedback" data-testid="export-text-warning">
            <v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" />
            <span>{{ t("export.textWarning") }}</span>
          </p>
        </template>
        <p v-if="result.omitted.length > 0" class="text-body-medium" data-testid="export-omitted">{{ t("export.omitted", { names: result.omitted.map(fieldName).join(t("list.separator")) }) }}</p>
        <pre class="p-log" data-testid="export-json">{{ result.json }}</pre>
        <p class="text-body-small">{{ t("export.formatNote") }}</p>
        <SaveMock :target="presetName(preset)" :file-name="result.fileName" :format-label="t('export.format')" testid="preset" @back="emit('close')" />
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
