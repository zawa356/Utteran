<script setup lang="ts">
import { computed } from "vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import type { ExpertPane } from "../../domain/shell";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";

/** Expert C structure only (UI-2): no inputs, no submission, no fake queue. */
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
          <h2 class="u-subheading">{{ t("expert.inputs") }}</h2>
          <v-alert type="info">{{ t("expert.inputsNotMigrated") }}</v-alert>
        </v-card>
      </section>
      <section :class="current('inspector')" data-pane="inspector" :aria-label="t('expert.inspector')" data-testid="pane-inspector">
        <v-card class="pa-3"><EnvironmentPanel /></v-card>
      </section>
      <section :class="current('queue')" data-pane="queue" :aria-label="t('expert.queue')" data-testid="pane-queue">
        <v-card class="pa-3 u-stack">
          <h2 class="u-subheading">{{ t("expert.queue") }}</h2>
          <v-alert type="info">{{ t("expert.queueNotMigrated") }}</v-alert>
        </v-card>
      </section>
    </div>
  </div>
</template>
