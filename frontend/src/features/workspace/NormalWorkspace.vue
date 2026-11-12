<script setup lang="ts">
import { useCore, useI18n } from "../../composables/core";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import TranscribeInput from "../transcribe/TranscribeInput.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";
import V1Job from "../transcribe/V1Job.vue";
import V1Settings from "../transcribe/V1Settings.vue";

/**
 * Normal mode: one file → settings → run, with the S2 environment panel below (same controllers as
 * Expert). In v1 mode (`--next-ui-v1`, S5-A) the settings and the run use the v1 path only.
 */
const { t } = useI18n();
const v1 = useCore().apiMode === "v1-dev";
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-heading">{{ t("transcribe.title") }}</h1>
    <v-alert v-if="v1" type="warning" density="compact" data-testid="v1-mode-notice">{{ t("v1.mode.notice") }}</v-alert>
    <v-alert v-else type="info" density="compact" data-testid="transcribe-not-migrated">{{ t("transcribe.notMigrated") }}</v-alert>
    <v-card class="pa-3"><TranscribeInput /></v-card>
    <template v-if="v1">
      <v-card class="pa-3"><V1Settings /></v-card>
      <v-card class="pa-3"><V1Job /></v-card>
    </template>
    <template v-else>
      <v-card class="pa-3"><TranscribeOptions /></v-card>
      <v-card class="pa-3"><TranscribeJob /></v-card>
    </template>
    <v-card class="pa-3"><EnvironmentPanel /></v-card>
  </section>
</template>
