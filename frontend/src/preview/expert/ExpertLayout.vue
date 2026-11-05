<script setup lang="ts">
import { mdiArrowExpandHorizontal } from "@mdi/js";
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import ResizeHandle from "../components/ResizeHandle.vue";
import { usePreview } from "../composables";
import { type ExpertPane, HANDLE_PX, paneRange, resolvePanes } from "../domain/layout";
import InputsPane from "./InputsPane.vue";
import InspectorPane from "./InspectorPane.vue";
import QueuePane from "./QueuePane.vue";

/**
 * Expert work area: inputs | Inspector | submit & status, with draggable (and keyboard-operable)
 * separators. Column count follows the WIDTH OF THIS AREA (not the viewport), so a wide sidebar
 * also drops columns: 3 → 2 (queue below) → 1 (pane switcher). Widths are clamped to minimums,
 * so nothing overflows horizontally. F6 / Shift+F6 move between panes.
 */
const { layout, layoutState, t } = usePreview();
const ORDER: ExpertPane[] = ["inputs", "inspector", "queue"];
const area = ref<HTMLElement | null>(null);
const areaWidth = ref(0);
const dragging = ref(false);
let observer: ResizeObserver | null = null;
let frame = 0;
onMounted(() => {
  if (!area.value) return;
  areaWidth.value = area.value.clientWidth;
  observer = new ResizeObserver((entries) => {
    const w = entries[0]?.contentRect.width;
    if (w === undefined) return;
    // Apply on the next frame, so the column change never re-enters this observer's delivery.
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => (areaWidth.value = w));
  });
  observer.observe(area.value);
});
onBeforeUnmount(() => {
  cancelAnimationFrame(frame);
  observer?.disconnect();
});

const widths = computed(() => resolvePanes(areaWidth.value, layoutState.value.panes));
const columns = computed(() => widths.value.columns);
const range = (pane: "inputs" | "queue") => paneRange(areaWidth.value, layoutState.value.panes, pane);
const gridStyle = computed(() => {
  const w = widths.value;
  if (w.columns === 3) return { gridTemplateColumns: `${w.inputs}px ${HANDLE_PX}px minmax(0, 1fr) ${HANDLE_PX}px ${w.queue}px` };
  if (w.columns === 2) return { gridTemplateColumns: `${w.inputs}px ${HANDLE_PX}px minmax(0, 1fr)` };
  return { gridTemplateColumns: "minmax(0, 1fr)" };
});
const panes = computed(() => ORDER.map((value) => ({ value, title: t(`expert.${value}`) })));
const refs = ref<Record<ExpertPane, HTMLElement | null>>({ inputs: null, inspector: null, queue: null });
const visible = (pane: ExpertPane) => columns.value > 1 || layoutState.value.expertPane === pane;

async function nextPane(event: KeyboardEvent) {
  if (event.key !== "F6") return;
  event.preventDefault();
  const active = document.activeElement;
  const current = ORDER.findIndex((p) => refs.value[p]?.contains(active));
  const from = current >= 0 ? current : ORDER.indexOf(layoutState.value.expertPane);
  const target = ORDER[(from + (event.shiftKey ? ORDER.length - 1 : 1)) % ORDER.length]!;
  layout.showPane(target);
  await nextTick();
  refs.value[target]?.focus();
}
</script>

<template>
  <div class="p-expert" :class="`p-expert--cols-${columns}`" :data-columns="columns" data-testid="expert-layout" @keydown="nextPane">
    <div class="p-expert__head">
      <h1 class="u-heading">{{ t("section.transcribe") }}</h1>
      <span class="text-body-small">{{ t("expert.f6") }}</span>
      <v-spacer />
      <v-btn size="small" variant="text" :prepend-icon="mdiArrowExpandHorizontal" data-testid="panes-reset" @click="layout.resetPanes()">{{ t("expert.resetPanes") }}</v-btn>
    </div>
    <v-btn-toggle
      v-if="columns === 1"
      class="mx-3 mb-2"
      :model-value="layoutState.expertPane"
      mandatory
      density="compact"
      variant="outlined"
      divided
      color="primary"
      :aria-label="t('expert.paneSwitcher')"
      data-testid="expert-pane-switch"
      @update:model-value="(v: ExpertPane) => layout.showPane(v)"
    >
      <v-btn v-for="p in panes" :key="p.value" :value="p.value" size="small" :data-testid="`pane-switch-${p.value}`">{{ p.title }}</v-btn>
    </v-btn-toggle>
    <div ref="area" class="p-expert__panes" :class="{ 'p-no-select': dragging }" :style="gridStyle" data-testid="expert-panes">
      <section
        v-show="visible('inputs')"
        id="p-pane-inputs"
        :ref="(el) => (refs.inputs = el as HTMLElement)"
        class="p-pane"
        data-pane="inputs"
        tabindex="-1"
        :aria-label="t('expert.inputs')"
        data-testid="pane-inputs"
      >
        <InputsPane />
      </section>
      <ResizeHandle
        v-if="columns > 1"
        :value="widths.inputs"
        :min="range('inputs').min"
        :max="range('inputs').max"
        :label="t('expert.resizeInputs')"
        controls="p-pane-inputs"
        testid="resize-inputs"
        @resize="(px) => layout.setPaneWidth('inputs', px, areaWidth)"
        @reset="layout.resetPanes()"
        @dragging="(v) => (dragging = v)"
      />
      <section
        v-show="visible('inspector')"
        :ref="(el) => (refs.inspector = el as HTMLElement)"
        class="p-pane"
        data-pane="inspector"
        tabindex="-1"
        :aria-label="t('expert.inspector')"
        data-testid="pane-inspector"
      >
        <InspectorPane />
      </section>
      <ResizeHandle
        v-if="columns === 3"
        :value="widths.queue"
        :min="range('queue').min"
        :max="range('queue').max"
        :label="t('expert.resizeQueue')"
        controls="p-pane-queue"
        invert
        testid="resize-queue"
        @resize="(px) => layout.setPaneWidth('queue', px, areaWidth)"
        @reset="layout.resetPanes()"
        @dragging="(v) => (dragging = v)"
      />
      <section
        v-show="visible('queue')"
        id="p-pane-queue"
        :ref="(el) => (refs.queue = el as HTMLElement)"
        class="p-pane p-pane--queue"
        data-pane="queue"
        tabindex="-1"
        :aria-label="t('expert.queue')"
        data-testid="pane-queue"
      >
        <QueuePane />
      </section>
    </div>
  </div>
</template>
