<script setup lang="ts">
import { computed } from "vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import type { ExpertPane } from "../../domain/shell";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import TranscribeInput from "../transcribe/TranscribeInput.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";

/**
 * Expert C structure (UI-2): ① the S3-A single input, ② processing settings and the S2
 * environment details, ③ the current job. Same controllers as Normal (no copy of the state);
 * the multi-input list, per-file overrides and the multi-job queue are S3-B.
 */
const { shell } = useCore();
const state = useStore(shell.store);
const { t } = useI18n();
const panes = computed(() => [
  { value: "inputs" as ExpertPane, title: t("expert.inputs") },
  { value: "inspector" as ExpertPane, title: t("expert.inspector") },
  { value: "queue" as ExpertPane, title: t("expert.queue") },
]);
const current = (pane: ExpertPane) => ({ "u-pane": true, "u-pane--current": state.value.expertPane === pane });
</script>

<template>
  <div data-testid="expert-workspace">
    <v-btn-toggle
      class="u-pane-switch ma-3"
      :model-value="state.expertPane"
      mandatory
      :aria-label="t('expert.paneSwitcher')"
      data-testid="expert-pane-switch"
      @update:model-value="(v: ExpertPane) => shell.showPane(v)"
    >
      <v-btn v-for="p in panes" :key="p.value" :value="p.value" size="small">{{ p.title }}</v-btn>
    </v-btn-toggle>
    <div class="u-panes">
      <section :class="current('inputs')" data-pane="inputs" :aria-label="t('expert.inputs')" data-testid="pane-inputs">
        <v-card class="pa-3 u-stack">
          <v-alert type="info" density="compact">{{ t("expert.inputsNotMigrated") }}</v-alert>
          <TranscribeInput />
        </v-card>
      </section>
      <section :class="current('inspector')" data-pane="inspector" :aria-label="t('expert.inspector')" data-testid="pane-inspector">
        <v-card class="pa-3"><TranscribeOptions /></v-card>
        <v-card class="pa-3 mt-3"><EnvironmentPanel detail /></v-card>
      </section>
      <section :class="current('queue')" data-pane="queue" :aria-label="t('expert.queue')" data-testid="pane-queue">
        <v-card class="pa-3 u-stack">
          <v-alert type="info" density="compact">{{ t("expert.queueNotMigrated") }}</v-alert>
          <TranscribeJob />
        </v-card>
      </section>
    </div>
  </div>
</template>
