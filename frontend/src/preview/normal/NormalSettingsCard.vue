<script setup lang="ts">
import SettingsFields from "../components/SettingsFields.vue";
import { usePreview } from "../composables";
import { useNormalFile } from "./useNormalFile";

/**
 * Everyday settings (language, diarization, formats); model and speaker count behind "details".
 * The fields show what THIS file will run with, and an edit applies to THIS file only (iteration 4,
 * DemoController.setNormalSetting) — files prepared in Expert are never changed from here.
 */
const { t } = usePreview();
const { inputId } = useNormalFile();
</script>

<template>
  <v-card class="p-card" tag="section" aria-labelledby="normal-settings-heading" data-testid="normal-settings">
    <div class="p-card__head"><h2 id="normal-settings-heading" class="p-card__title">{{ t("normal.settings") }}</h2></div>
    <SettingsFields :input-id="inputId" :fields="['language', 'diarization', 'formats']" />
    <v-expansion-panels variant="accordion" flat>
      <v-expansion-panel data-testid="config-details">
        <v-expansion-panel-title>{{ t("config.details") }}</v-expansion-panel-title>
        <v-expansion-panel-text>
          <SettingsFields :input-id="inputId" :fields="['model', 'speakerMode']" />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>
