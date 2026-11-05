<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";

/**
 * Small, dependency-free splitter (UI-3-0 iteration 2). Pointer drag with pointer capture,
 * text selection suppressed only while dragging, listeners removed on release/cancel/unmount.
 * Keyboard (WAI-ARIA window splitter): ←/→ = 16 px (Shift = 64 px), Home/End = min/max,
 * Enter = reset. Double-click also resets. The parent owns the value and the clamping.
 * `invert` = the sized element is on the right of the handle (dragging right makes it smaller).
 */
const props = withDefaults(
  defineProps<{ value: number; min: number; max: number; label: string; controls?: string; invert?: boolean; testid: string; step?: number }>(),
  { invert: false, controls: undefined, step: 16 },
);
const emit = defineEmits<{ resize: [px: number]; reset: []; dragging: [active: boolean] }>();

const el = ref<HTMLElement | null>(null);
const dragging = ref(false);
let startX = 0;
let startValue = 0;
let pointerId: number | null = null;

function onMove(e: PointerEvent) {
  if (e.pointerId !== pointerId) return;
  const delta = (e.clientX - startX) * (props.invert ? -1 : 1);
  emit("resize", startValue + delta);
}

function stop() {
  const node = el.value;
  if (node) {
    node.removeEventListener("pointermove", onMove);
    node.removeEventListener("pointerup", stop);
    node.removeEventListener("pointercancel", stop);
    node.removeEventListener("lostpointercapture", stop);
    if (pointerId !== null && node.hasPointerCapture(pointerId)) node.releasePointerCapture(pointerId);
  }
  if (dragging.value) emit("dragging", false);
  document.documentElement.classList.remove("p-resizing");
  dragging.value = false;
  pointerId = null;
}

function onDown(e: PointerEvent) {
  if (e.button !== 0 || !el.value) return;
  e.preventDefault();
  startX = e.clientX;
  startValue = props.value;
  pointerId = e.pointerId;
  el.value.setPointerCapture(e.pointerId);
  el.value.addEventListener("pointermove", onMove);
  el.value.addEventListener("pointerup", stop);
  el.value.addEventListener("pointercancel", stop);
  el.value.addEventListener("lostpointercapture", stop);
  document.documentElement.classList.add("p-resizing");
  dragging.value = true;
  emit("dragging", true);
  el.value.focus();
}

function onKey(e: KeyboardEvent) {
  const step = e.shiftKey ? props.step * 4 : props.step;
  const sign = props.invert ? -1 : 1;
  let next: number | null = null;
  if (e.key === "ArrowLeft") next = props.value - step * sign;
  else if (e.key === "ArrowRight") next = props.value + step * sign;
  else if (e.key === "Home") next = props.min;
  else if (e.key === "End") next = props.max;
  else if (e.key === "Enter") {
    e.preventDefault();
    emit("reset");
    return;
  }
  if (next === null) return;
  e.preventDefault();
  emit("resize", next);
}

onBeforeUnmount(stop);
</script>

<template>
  <div
    ref="el"
    class="p-resize"
    :class="{ 'p-resize--active': dragging }"
    role="separator"
    aria-orientation="vertical"
    tabindex="0"
    :aria-label="label"
    :aria-controls="controls"
    :aria-valuenow="Math.round(value)"
    :aria-valuemin="Math.round(min)"
    :aria-valuemax="Math.round(max)"
    :title="label"
    :data-testid="testid"
    @pointerdown="onDown"
    @dblclick="emit('reset')"
    @keydown="onKey"
  >
    <span class="p-resize__grip" aria-hidden="true"></span>
  </div>
</template>
