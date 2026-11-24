<script setup lang="ts">
import { useCore, useI18n } from "../../composables/core";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import TranscribeInput from "../transcribe/TranscribeInput.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";
import V1Job from "../transcribe/V1Job.vue";
import V1Settings from "../transcribe/V1Settings.vue";
import WorksetBar from "../transcribe/WorksetBar.vue";

/**
 * Normal mode: one file → settings → run, with the S2 environment panel below (same controllers as
 * Expert). In v1 mode (`--next-ui-v1`, S5-A) the settings and the run use the v1 path only.
 */
const { t } = useI18n();
const v1 = useCore().apiMode === "v1-dev";
</script>

<template>
  <section class="u-page u-stack" :class="{ 'u-normal-layout': v1 }" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-heading">{{ t("transcribe.title") }}</h1>
    <p v-if="v1" class="text-body-2 text-medium-emphasis" data-testid="v1-mode-notice">{{ t("v1.mode.notice") }}</p>
    <v-alert v-else type="info" density="compact" data-testid="transcribe-not-migrated">{{ t("transcribe.notMigrated") }}</v-alert>
    <v-card v-if="v1" class="pa-3"><WorksetBar /></v-card>
    <div v-if="v1" class="u-normal-grid">
      <div class="u-stack">
        <v-card class="pa-3"><TranscribeInput /></v-card>
        <v-card class="pa-3"><V1Settings /></v-card>
        <v-card class="pa-3"><EnvironmentPanel /></v-card>
      </div>
      <v-card class="pa-3"><V1Job /></v-card>
    </div>
    <template v-else>
      <v-card class="pa-3"><TranscribeInput /></v-card>
      <v-card class="pa-3"><TranscribeOptions /></v-card>
      <v-card class="pa-3"><TranscribeJob /></v-card>
      <v-card class="pa-3"><EnvironmentPanel /></v-card>
    </template>
  </section>
</template>

<style scoped>
.u-normal-layout {
  max-width: 1480px;
  margin-inline: auto;
  container-type: inline-size;
}
.u-normal-grid {
  display: grid;
  align-items: start;
  gap: var(--u-gap);
  grid-template-columns: minmax(0, 1fr);
}
@container (min-width: 940px) {
  .u-normal-grid {
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  }
}
</style>
