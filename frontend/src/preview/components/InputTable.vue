<script setup lang="ts">
import { mdiAlertOutline, mdiCheckCircleOutline, mdiCircleOutline, mdiClockOutline, mdiPlayCircleOutline, mdiCancel, mdiAlertCircleOutline, mdiProgressWrench } from "@mdi/js";
import { nextTick, ref } from "vue";
import { usePreview } from "../composables";
import { jobOf, overrideCount } from "../domain/demo";
import { type DemoInput, formatDuration } from "../domain/fixtures";

/**
 * Explorer "details view"-like list: one 28 px row per input with name, format, length, state and
 * override count. Narrow panes hide the format column first (container query). Long names are
 * cut with an ellipsis; the full name is in the tooltip (title) and in the Inspector header.
 * Keyboard (grid pattern): one tab stop; the arrow keys, Home and End move; Space toggles; Enter selects only
 * this row; click = select only, Ctrl/⌘+click or the checkbox = add/remove.
 */
const { demo, state, t } = usePreview();
const rows = ref<HTMLElement[]>([]);
const cursor = ref(0);

function status(input: DemoInput): { icon: string; text: string; tone: string } {
  if (!input.supported) return { icon: mdiAlertOutline, text: t("input.status.unsupported"), tone: "warning" };
  const job = jobOf(state.value, input.id);
  if (!job) return { icon: mdiCircleOutline, text: t("input.status.notSubmitted"), tone: "" };
  const icons = { waiting: mdiClockOutline, preparing: mdiProgressWrench, running: mdiPlayCircleOutline, succeeded: mdiCheckCircleOutline, failed: mdiAlertCircleOutline, cancelled: mdiCancel };
  const tones = { waiting: "", preparing: "info", running: "primary", succeeded: "success", failed: "error", cancelled: "" };
  return { icon: icons[job.state], text: t(`job.state.${job.state}`), tone: tones[job.state] };
}

async function focusRow(index: number) {
  const max = state.value.inputs.length - 1;
  cursor.value = Math.max(0, Math.min(index, max));
  await nextTick();
  rows.value[cursor.value]?.focus();
}

function onKey(e: KeyboardEvent, index: number, input: DemoInput) {
  if (e.key === "ArrowDown") void focusRow(index + 1);
  else if (e.key === "ArrowUp") void focusRow(index - 1);
  else if (e.key === "Home") void focusRow(0);
  else if (e.key === "End") void focusRow(state.value.inputs.length - 1);
  else if (e.key === " ") demo.toggleSelect(input.id);
  else if (e.key === "Enter") demo.selectOnly(input.id);
  else return;
  e.preventDefault();
}

function onClick(e: MouseEvent, index: number, input: DemoInput) {
  cursor.value = index;
  if (e.ctrlKey || e.metaKey) demo.toggleSelect(input.id);
  else demo.selectOnly(input.id);
}
</script>

<template>
  <div class="p-explorer-wrap">
    <p id="p-inputs-help" class="p-sr-only">{{ t("inputs.tableHelp") }}</p>
    <table class="p-explorer" role="grid" aria-multiselectable="true" :aria-label="t('inputs.listLabel')" aria-describedby="p-inputs-help" :aria-rowcount="state.inputs.length + 1" data-testid="input-list">
      <thead>
        <tr>
          <th class="p-explorer__check" scope="col"><span class="p-sr-only">{{ t("inputs.col.select") }}</span></th>
          <th scope="col">{{ t("inputs.col.name") }}</th>
          <th class="p-explorer__fmt" scope="col">{{ t("inputs.col.format") }}</th>
          <th class="p-explorer__len" scope="col">{{ t("inputs.col.duration") }}</th>
          <th class="p-explorer__state" scope="col" :title="t('inputs.col.status')"><span class="p-explorer__state-text">{{ t("inputs.col.status") }}</span></th>
          <th class="p-explorer__ovr" scope="col" :title="t('inputs.col.overrideLong')"><span class="p-explorer__state-text">{{ t("inputs.col.override") }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(input, index) in state.inputs"
          :key="input.id"
          :ref="(el) => (rows[index] = el as HTMLElement)"
          :tabindex="index === Math.min(cursor, state.inputs.length - 1) ? 0 : -1"
          :aria-selected="state.selected.includes(input.id)"
          :class="{ 'p-explorer__row--selected': state.selected.includes(input.id), 'p-explorer__row--muted': !input.supported }"
          :data-testid="`input-row-${input.id}`"
          @click="(e) => onClick(e, index, input)"
          @keydown="(e) => onKey(e, index, input)"
          @focus="cursor = index"
        >
          <td class="p-explorer__check">
            <v-checkbox-btn
              :model-value="state.selected.includes(input.id)"
              tabindex="-1"
              :aria-label="t('input.select', { name: input.name })"
              @click.stop="demo.toggleSelect(input.id)"
            />
          </td>
          <td class="p-explorer__name"><span class="p-ellipsis" :title="`${input.name} · ${input.format.toUpperCase()} · ${formatDuration(input.durationSec)}`">{{ input.name }}</span></td>
          <td class="p-explorer__fmt">{{ input.format.toUpperCase() }}</td>
          <td class="p-explorer__len">{{ formatDuration(input.durationSec) }}</td>
          <td class="p-explorer__state" :class="status(input).tone ? `text-${status(input).tone}` : ''" :title="status(input).text" :data-testid="`input-row-${input.id}-state`">
            <v-icon :icon="status(input).icon" size="small" aria-hidden="true" />
            <span class="p-ellipsis p-explorer__state-text">{{ status(input).text }}</span>
          </td>
          <td class="p-explorer__ovr">
            <span v-if="overrideCount(state, input.id) > 0" class="p-ovr" :title="t('input.overrides', { count: overrideCount(state, input.id) })" :data-testid="`input-row-${input.id}-override`">
              {{ t("input.overridesShort", { count: overrideCount(state, input.id) }) }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
