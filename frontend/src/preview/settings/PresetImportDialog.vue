<script setup lang="ts">
import { mdiAlertCircleOutline, mdiAlertOutline, mdiFileDocumentOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { allPresets } from "../domain/demo";
import { IMPORT_SAMPLES, type ImportIssue, parsePresetDocument, presetKeys, uniqueName } from "../domain/presets";
import type { PreviewKey } from "../i18n";
import { usePresetName } from "./usePresetName";
import { useSettingText } from "./useSettingText";

/**
 * Import a preset. The product opens the OS file picker; the prototype offers fixed SAMPLE files
 * instead (valid, duplicate name, old version, broken JSON, unsupported values) and shows what
 * would happen with each. Nothing is read from disk.
 */
const emit = defineEmits<{ close: []; imported: [id: string] }>();
const { demo, state, t } = usePreview();
const { presetName } = usePresetName();
const { valueText, fieldName } = useSettingText();
const sampleId = ref<string | null>(null);
const name = ref("");
const sample = computed(() => IMPORT_SAMPLES.find((s) => s.id === sampleId.value) ?? null);
const result = computed(() => (sample.value ? parsePresetDocument(sample.value.text) : null));
const taken = computed(() => allPresets(state.value).map((p) => presetName(p)));
const duplicate = computed(() => Boolean(result.value?.ok && result.value.name && taken.value.includes(result.value.name)));
function choose(id: string) {
  sampleId.value = id;
  const r = parsePresetDocument(IMPORT_SAMPLES.find((s) => s.id === id)!.text);
  name.value = r.name ? uniqueName(r.name, taken.value) : "";
}
function load() {
  if (!result.value?.ok) return;
  emit("imported", demo.importPreset(name.value.trim(), result.value.values));
}
const issueText = (i: ImportIssue) => t(`import.issue.${i.code}` as PreviewKey, i.params);
</script>

<template>
  <v-dialog :model-value="true" max-width="640" scrollable @update:model-value="(v) => !v && emit('close')">
    <v-card variant="elevated" :title="t('preset.importTitle')" data-testid="preset-import-dialog">
      <v-card-text class="u-stack">
        <template v-if="!sample">
          <p class="text-body-medium">{{ t("import.choose") }}</p>
          <v-list lines="one" :aria-label="t('import.samples')" data-testid="import-samples">
            <v-list-item v-for="s in IMPORT_SAMPLES" :key="s.id" :prepend-icon="mdiFileDocumentOutline" :title="s.fileName" :data-testid="`import-sample-${s.id}`" @click="choose(s.id)" />
          </v-list>
        </template>
        <template v-else-if="result">
          <div class="text-body-medium" data-testid="import-file">{{ t("import.file", { name: sample.fileName }) }}</div>
          <template v-if="result.ok">
            <v-text-field v-model="name" :label="t('preset.name')" maxlength="120" data-testid="import-name" />
            <p v-if="duplicate" class="p-feedback" data-testid="import-duplicate">
              <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
              <span>{{ t("import.duplicate", { name: result.name ?? "" }) }}</span>
            </p>
            <dl class="p-dl text-body-medium" data-testid="import-preview">
              <template v-for="k in presetKeys(result)" :key="k">
                <dt>{{ fieldName(k) }}</dt>
                <dd>{{ valueText(k, result.values[k]) }}</dd>
              </template>
            </dl>
            <p v-for="w in result.warnings" :key="String(w.params.field)" class="p-feedback" data-testid="import-warning">
              <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
              <span>{{ t("import.warning.model", w.params) }}</span>
            </p>
          </template>
          <div v-else class="u-stack" role="alert" data-testid="import-error">
            <p class="p-feedback">
              <v-icon :icon="mdiAlertCircleOutline" color="error" size="small" aria-hidden="true" />
              <span class="font-weight-medium">{{ t("import.failed") }}</span>
            </p>
            <ul class="p-bullets">
              <li v-for="(i, n) in result.issues" :key="n" :data-testid="`import-issue-${i.code}`">{{ issueText(i) }}</li>
            </ul>
            <p class="text-body-medium">{{ t("import.failedHint") }}</p>
          </div>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="sample" variant="text" data-testid="import-other" @click="sampleId = null">{{ t("import.other") }}</v-btn>
        <v-spacer />
        <v-btn variant="text" data-testid="import-cancel" @click="emit('close')">{{ t("common.cancel") }}</v-btn>
        <v-btn v-if="result?.ok" color="primary" variant="tonal" :disabled="name.trim() === '' || taken.includes(name.trim())" data-testid="import-load" @click="load">{{ t("import.load") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
