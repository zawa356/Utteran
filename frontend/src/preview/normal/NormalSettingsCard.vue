<script setup lang="ts">
import SettingsFields from "../components/SettingsFields.vue";
import { usePreview } from "../composables";
import { useNormalFile } from "./useNormalFile";

/**
 * Everyday settings (language, diarization, formats) with model and speaker count behind
 * "details". These edit the common defaults; a file with Expert overrides says so (they win).
 */
const { t } = usePreview();
const { overrides } = useNormalFile();
</script>

<template>
  <v-card class="pa-3" tag="section" aria-labelledby="normal-settings-heading" data-testid="normal-settings">
    <h2 id="normal-settings-heading" class="u-subheading mb-2">{{ t("normal.settings") }}</h2>
    <SettingsFields mode="common" :fields="['language', 'diarization', 'formats']" compact />
    <v-alert v-if="overrides > 0" type="info" variant="outlined" class="mt-2" data-testid="config-overrides-note">{{ t("normal.overridesNote", { count: overrides }) }}</v-alert>
    <v-expansion-panels class="mt-2" variant="accordion" flat>
      <v-expansion-panel data-testid="config-details">
        <v-expansion-panel-title>{{ t("config.details") }}</v-expansion-panel-title>
        <v-expansion-panel-text>
          <SettingsFields mode="common" :fields="['model', 'speakers']" compact />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>
