<script setup lang="ts">
import { useCore, useI18n } from "../../composables/core";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import TranscribeInput from "../transcribe/TranscribeInput.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";
import V1Job from "../transcribe/V1Job.vue";
import V1OutputFolder from "../transcribe/V1OutputFolder.vue";
import WorksetBar from "../transcribe/WorksetBar.vue";
import NormalSettingsCard from "./NormalSettingsCard.vue";

/**
 * Normal mode: one file → settings → run, with the S2 environment panel below (same controllers as
 * Expert). In v1 mode (`--next-ui-v1`, S5-A) the screen follows the Design Freeze (#96): left = the
 * file (with its output folder) and the few everyday settings, right = check/submit, status and
 * result. The settings and the run use the v1 path only; the legacy shape stays unchanged.
 */
const { t } = useI18n();
const v1 = useCore().apiMode === "v1-dev";
</script>

<template>
  <section v-if="v1" class="u-page u-normal-layout" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-sr-only">{{ t("transcribe.title") }}</h1>
    <WorksetBar compact class="u-workset-strip" />
    <p class="text-caption text-medium-emphasis u-mode-notice" data-testid="v1-mode-notice">{{ t("v1.mode.notice") }}</p>
    <div class="u-normal-grid">
      <div class="u-normal-col">
        <v-card class="u-fcard" tag="section" aria-labelledby="normal-file-heading" data-testid="normal-file">
          <div class="u-fcard__head"><h2 id="normal-file-heading" class="u-fcard__title">{{ t("ui3.normal.file") }}</h2></div>
          <TranscribeInput bare />
          <V1OutputFolder />
        </v-card>
        <NormalSettingsCard />
      </div>
      <v-card class="u-fcard" tag="section" aria-labelledby="normal-run-heading" data-testid="normal-run">
        <div class="u-fcard__head"><h2 id="normal-run-heading" class="u-fcard__title">{{ t("ui3.normal.run") }}</h2></div>
        <V1Job bare />
      </v-card>
    </div>
  </section>
  <section v-else class="u-page u-stack" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-heading">{{ t("transcribe.title") }}</h1>
    <v-alert type="info" density="compact" data-testid="transcribe-not-migrated">{{ t("transcribe.notMigrated") }}</v-alert>
    <v-card class="pa-3"><TranscribeInput /></v-card>
    <v-card class="pa-3"><TranscribeOptions /></v-card>
    <v-card class="pa-3"><TranscribeJob /></v-card>
    <v-card class="pa-3"><EnvironmentPanel /></v-card>
  </section>
</template>

<style scoped>
.u-normal-layout {
  max-width: 1180px;
  padding: 12px 20px 20px;
  container-type: inline-size;
}
.u-normal-grid {
  display: grid;
  align-items: start;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr);
}
.u-normal-col > * + * {
  margin-top: 16px;
}
.u-workset-strip {
  margin-bottom: 4px;
}
.u-mode-notice {
  margin: 0 0 12px;
}
@container (min-width: 880px) {
  .u-normal-grid {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}
</style>
