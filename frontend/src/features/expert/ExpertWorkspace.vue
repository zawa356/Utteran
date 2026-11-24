<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onUnmounted, useTemplateRef, watch } from "vue";
import ResizeHandle from "../../components/ResizeHandle.vue";
import { HANDLE_PX, paneRange, resolvePanes } from "../../domain/paneLayout";
import { useContentWidth } from "../../composables/useContentWidth";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import type { ExpertPane } from "../../domain/shell";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import BatchPanel from "../transcribe/BatchPanel.vue";
import InputList from "../transcribe/InputList.vue";
import InputOverrides from "../transcribe/InputOverrides.vue";
import TranscribeJob from "../transcribe/TranscribeJob.vue";
import TranscribeOptions from "../transcribe/TranscribeOptions.vue";
import RowGlossary from "../transcribe/RowGlossary.vue";
import V1Batch from "../transcribe/V1Batch.vue";
import V1Job from "../transcribe/V1Job.vue";
import V1JobBoard from "../transcribe/V1JobBoard.vue";
import V1Settings from "../transcribe/V1Settings.vue";

const { shell, apiMode } = useCore();
const v1 = apiMode === "v1-dev";
const state = useStore(shell.store);
const { t } = useI18n();
const order: ExpertPane[] = ["inputs", "inspector", "queue"];
const area = useTemplateRef("area");
const areaWidth = useContentWidth(area, v1);
const widths = computed(() => resolvePanes(areaWidth.value, state.value.paneRatios));
const columns = computed(() => widths.value.columns);

const gridStyle = computed(() => {
  const w = widths.value;
  if (w.columns === 3) return { gridTemplateColumns: `${w.inputs}px ${HANDLE_PX}px minmax(0, 1fr) ${HANDLE_PX}px ${w.queue}px` };
  if (w.columns === 2) return { gridTemplateColumns: `${w.inputs}px ${HANDLE_PX}px minmax(0, 1fr)` };
  return { gridTemplateColumns: "minmax(0, 1fr)" };
});

const range = (pane: "inputs" | "queue") => paneRange(areaWidth.value, state.value.paneRatios, pane);
const selected = computed(() => v1 && columns.value === 2 ? state.value.expertRightPane : state.value.expertPane);
const title = (pane: ExpertPane) => {
  if (v1 && pane === "inputs") return t("expert.paneInputs");
  if (v1 && pane === "queue") return t("expert.paneQueue");
  return t(`expert.${pane}`);
};
const panes = computed(() => (v1 && columns.value === 2 ? order.slice(1) : order).map((value) => ({
  value, title: v1 ? `${order.indexOf(value) + 1} ${title(value)}` : title(value),
})));
const visible = (pane: ExpertPane) => !v1 || columns.value === 3 || (columns.value === 2 && pane === "inputs") || selected.value === pane;
const current = (pane: ExpertPane) => ({ "u-pane": true, "u-pane--current": state.value.expertPane === pane });
const node = (pane: ExpertPane) => area.value?.querySelector<HTMLElement>(`[data-pane="${pane}"]`);
const focusedPane = () => order.find((pane) => node(pane)?.contains(document.activeElement));

async function moveFocus(pane: ExpertPane) {
  shell.showPane(pane);
  await nextTick();
  node(pane)?.focus({ preventScroll: true });
}

function cyclePane(event: KeyboardEvent) {
  if (event.key !== "F6" || event.altKey || event.ctrlKey || event.metaKey || event.isComposing || event.keyCode === 229) return;
  if (document.activeElement?.closest('[role="dialog"], [role="listbox"], [role="menu"]')) return;
  event.preventDefault();
  const from = order.indexOf(focusedPane() ?? selected.value);
  void moveFocus(order[(from + (event.shiftKey ? 2 : 1)) % order.length]!);
}

if (v1) {
  onActivated(() => window.addEventListener("keydown", cyclePane));
  const detach = () => window.removeEventListener("keydown", cyclePane);
  onDeactivated(detach);
  onUnmounted(detach);

  // During a responsive change preserve the pane containing keyboard focus.
  watch(columns, () => {
    const pane = focusedPane();
    if (pane && !visible(pane)) void moveFocus(pane);
  });
}
</script>

<template>
  <div
    :class="v1 ? 'u-expert-layout' : 'u-expert-legacy'"
    :data-columns="v1 ? columns : undefined"
    data-testid="expert-workspace"
  >
    <v-btn-toggle
      v-if="!v1 || columns < 3"
      class="u-pane-switch"
      :model-value="selected"
      density="compact"
      variant="outlined"
      divided
      color="primary"
      mandatory
      :aria-label="t('expert.paneSwitcher')"
      data-testid="expert-pane-switch"
      @update:model-value="(v: ExpertPane) => shell.showPane(v)"
    >
      <v-btn v-for="p in panes" :key="p.value" :value="p.value" size="small" :data-testid="`pane-switch-${p.value}`">{{ p.title }}</v-btn>
    </v-btn-toggle>
    <div ref="area" class="u-panes" :style="v1 ? gridStyle : undefined" data-testid="expert-panes">
      <section v-show="visible('inputs')" id="u-pane-inputs" :class="current('inputs')" :tabindex="v1 ? -1 : undefined" data-pane="inputs" :aria-label="title('inputs')" data-testid="pane-inputs">
        <h2 v-if="v1" class="u-pane-title">
          <span aria-hidden="true">1</span>{{ title("inputs") }}
        </h2>
        <v-card class="pa-3"><InputList /></v-card>
      </section>
      <ResizeHandle
        v-if="v1 && columns > 1"
        :value="widths.inputs"
        :min="range('inputs').min"
        :max="range('inputs').max"
        :label="t('expert.resizeInputs')"
        controls="u-pane-inputs"
        testid="resize-inputs"
        @resize="(px) => shell.resizePane('inputs', px, areaWidth)"
        @reset="shell.resetPanes()"
      />
      <section v-show="visible('inspector')" id="u-pane-inspector" :class="current('inspector')" :tabindex="v1 ? -1 : undefined" data-pane="inspector" :aria-label="title('inspector')" data-testid="pane-inspector">
        <h2 v-if="v1" class="u-pane-title">
          <span aria-hidden="true">2</span>{{ title("inspector") }}
        </h2>
        <v-card v-if="v1" class="pa-3"><V1Settings /></v-card>
        <v-card class="pa-3" :class="{ 'mt-3': v1 }">
          <InputOverrides />
          <RowGlossary v-if="v1" class="mt-2" />
          <p v-if="v1" class="mt-2 text-caption" data-testid="v1-overrides-note">{{ t("v1.overrides.note") }}</p>
        </v-card>
        <v-card v-if="!v1" class="pa-3 mt-3"><TranscribeOptions /></v-card>
        <v-card class="pa-3 mt-3"><EnvironmentPanel detail /></v-card>
      </section>
      <ResizeHandle
        v-if="v1 && columns === 3"
        :value="widths.queue"
        :min="range('queue').min"
        :max="range('queue').max"
        :label="t('expert.resizeQueue')"
        controls="u-pane-queue"
        testid="resize-queue"
        invert
        @resize="(px) => shell.resizePane('queue', px, areaWidth)"
        @reset="shell.resetPanes()"
      />
      <section v-show="visible('queue')" id="u-pane-queue" :class="current('queue')" :tabindex="v1 ? -1 : undefined" data-pane="queue" :aria-label="title('queue')" data-testid="pane-queue">
        <h2 v-if="v1" class="u-pane-title">
          <span aria-hidden="true">3</span>{{ title("queue") }}
        </h2>
        <template v-if="v1">
          <v-card class="pa-3"><V1Batch /></v-card>
          <v-card class="pa-3 mt-3"><V1JobBoard /></v-card>
          <details class="u-single-tools mt-3" data-testid="v1-single-tools">
            <summary data-testid="v1-single-tools-toggle">{{ t("expert.singleTools") }}</summary>
            <v-card class="pa-3 mt-2"><V1Job /></v-card>
          </details>
        </template>
        <template v-else>
          <v-card class="pa-3"><BatchPanel /></v-card>
          <v-card class="pa-3 mt-3 u-stack">
            <v-alert type="info" density="compact">{{ t("expert.queueNotMigrated") }}</v-alert>
            <TranscribeJob />
          </v-card>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.u-expert-layout {
  flex: 1 1 auto;
  min-height: 240px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 0 var(--u-gap) var(--u-gap);
}
.u-expert-layout .u-panes {
  display: grid;
  flex: 1 1 0;
  min-height: 0;
  min-width: 0;
}
.u-expert-layout .u-pane {
  min-width: 0;
  overflow-y: auto;
  overflow-wrap: anywhere;
  padding: 2px;
  scrollbar-width: thin;
  scroll-padding-block-start: 44px;
}
.u-pane-title {
  position: sticky;
  z-index: 1;
  top: 0;
  display: flex;
  gap: 8px;
  align-items: center;
  padding-block: 8px;
  font-size: 1rem;
  background: rgb(var(--v-theme-background));
}
.u-pane-title span {
  display: inline-grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 4px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.u-single-tools > summary {
  cursor: pointer;
  padding: 8px;
  font-size: 0.875rem;
  font-weight: 600;
}
.u-expert-layout .u-pane:focus-visible {
  outline-offset: -2px;
}
.u-expert-layout .u-pane-switch {
  align-self: start;
  margin: 0 0 8px;
  height: auto;
  flex-wrap: wrap;
  max-width: 100%;
}
.u-expert-layout[data-columns="2"] .u-pane-switch {
  align-self: end;
}
.u-expert-layout .u-pane-switch :deep(.v-btn) {
  padding-inline: 8px;
  letter-spacing: normal;
}
</style>
