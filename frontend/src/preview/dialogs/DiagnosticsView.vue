<script setup lang="ts">
import { mdiCheck, mdiMinusCircleOutline } from "@mdi/js";
import { ref } from "vue";
import { usePreview } from "../composables";
import { DIAGNOSTICS_EXCLUDED, DIAGNOSTICS_FILE_NAME, DIAGNOSTICS_INCLUDED } from "../domain/diagnostics";
import SaveMock from "./SaveMock.vue";

/**
 * Diagnostics export, simplified (iteration 4, owner memo #15): one sentence of purpose, one of
 * privacy, and "Save…". What is in / out stays available behind "What is included" for those who
 * want it — the exclusions themselves (paths, audio, text, tokens, prompts) are NOT relaxed.
 */
const { t } = usePreview();
const saving = ref(false);
</script>

<template>
  <div class="u-stack" data-testid="diagnostics-view">
    <template v-if="!saving">
      <p class="text-body-medium" data-testid="diag-lead">{{ t("dialog.diagnostics.lead") }}</p>
      <p class="text-body-medium" data-testid="diag-privacy">{{ t("dialog.diagnostics.privacy") }}</p>
      <v-expansion-panels variant="accordion" flat>
        <v-expansion-panel data-testid="diag-details">
          <v-expansion-panel-title>{{ t("dialog.diagnostics.details") }}</v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="font-weight-medium mb-1">{{ t("dialog.diagnostics.included") }}</div>
            <ul class="p-bullets" data-testid="diag-included">
              <li v-for="k in DIAGNOSTICS_INCLUDED" :key="k"><v-icon :icon="mdiCheck" size="small" color="success" aria-hidden="true" /> {{ t(k) }}</li>
            </ul>
            <div class="font-weight-medium mb-1 mt-3">{{ t("dialog.diagnostics.excluded") }}</div>
            <ul class="p-bullets" data-testid="diag-excluded">
              <li v-for="k in DIAGNOSTICS_EXCLUDED" :key="k"><v-icon :icon="mdiMinusCircleOutline" size="small" aria-hidden="true" /> {{ t(k) }}</li>
            </ul>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
      <div class="u-row">
        <v-spacer />
        <v-btn color="primary" data-testid="diag-export" @click="saving = true">{{ t("save.save") }}</v-btn>
      </div>
    </template>
    <SaveMock v-else :target="t('dialog.diagnostics.target')" :file-name="DIAGNOSTICS_FILE_NAME" :format-label="t('dialog.diagnostics.format')" testid="diag" @back="saving = false" />
  </div>
</template>
