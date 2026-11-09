<script setup lang="ts">
import { useI18n } from "../../composables/core";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import TranscribeInput from "../transcribe/TranscribeInput.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";

/** Normal mode: one file → settings → run, with the S2 environment panel below (same controllers as Expert). */
const { t } = useI18n();
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-heading">{{ t("transcribe.title") }}</h1>
    <v-alert type="info" density="compact" data-testid="transcribe-not-migrated">{{ t("transcribe.notMigrated") }}</v-alert>
    <v-card class="pa-3"><TranscribeInput /></v-card>
    <v-card class="pa-3"><TranscribeOptions /></v-card>
    <v-card class="pa-3"><TranscribeJob /></v-card>
    <v-card class="pa-3"><EnvironmentPanel /></v-card>
  </section>
</template>
