<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { usePreview } from "../composables";
import type { ExpertPane } from "../../domain/shell";
import InputsPane from "./InputsPane.vue";
import InspectorPane from "./InspectorPane.vue";
import QueuePane from "./QueuePane.vue";

/**
 * Expert mode C: inputs (23) / Inspector (45) / output & queue (32), reusing the UI-2 grid in
 * styles/app.css (3 columns ≥1280px, 2 columns + bottom row 1024–1279px, one pane + switcher
 * below 1024px). F6 moves focus to the next pane (and switches it in the one-pane layout).
 */
const { shell, shellState, t } = usePreview();
const ORDER: ExpertPane[] = ["inputs", "inspector", "queue"];
const panes = computed(() => ORDER.map((value) => ({ value, title: t(`expert.${value}`) })));
const refs = ref<Record<ExpertPane, HTMLElement | null>>({ inputs: null, inspector: null, queue: null });
const cls = (pane: ExpertPane) => ({ "u-pane": true, "p-pane": true, "u-pane--current": shellState.value.expertPane === pane });

async function nextPane(event: KeyboardEvent) {
  if (event.key !== "F6") return;
  event.preventDefault();
  const active = document.activeElement;
  const current = ORDER.findIndex((p) => refs.value[p]?.contains(active)) ;
  const from = current >= 0 ? current : ORDER.indexOf(shellState.value.expertPane);
  const target = ORDER[(from + (event.shiftKey ? ORDER.length - 1 : 1)) % ORDER.length]!;
  shell.showPane(target);
  await nextTick();
  refs.value[target]?.focus();
}
</script>

<template>
  <div data-testid="expert-layout" @keydown="nextPane">
    <div class="u-row px-3 pt-2">
      <h1 class="u-heading">{{ t("section.transcribe") }}</h1>
      <span class="text-caption">{{ t("expert.f6") }}</span>
    </div>
    <v-btn-toggle
      class="u-pane-switch ma-3"
      :model-value="shellState.expertPane"
      mandatory
      density="compact"
      variant="outlined"
      divided
      :aria-label="t('expert.paneSwitcher')"
      data-testid="expert-pane-switch"
      @update:model-value="(v: ExpertPane) => shell.showPane(v)"
    >
      <v-btn v-for="p in panes" :key="p.value" :value="p.value" size="small" :data-testid="`pane-switch-${p.value}`">{{ p.title }}</v-btn>
    </v-btn-toggle>
    <div class="u-panes">
      <section :ref="(el) => (refs.inputs = el as HTMLElement)" :class="cls('inputs')" data-pane="inputs" tabindex="-1" :aria-label="t('expert.inputs')" data-testid="pane-inputs">
        <v-card class="pa-3"><InputsPane /></v-card>
      </section>
      <section :ref="(el) => (refs.inspector = el as HTMLElement)" :class="cls('inspector')" data-pane="inspector" tabindex="-1" :aria-label="t('expert.inspector')" data-testid="pane-inspector">
        <v-card class="pa-3"><InspectorPane /></v-card>
      </section>
      <section :ref="(el) => (refs.queue = el as HTMLElement)" :class="cls('queue')" data-pane="queue" tabindex="-1" :aria-label="t('expert.queue')" data-testid="pane-queue">
        <v-card class="pa-3"><QueuePane /></v-card>
      </section>
    </div>
  </div>
</template>
