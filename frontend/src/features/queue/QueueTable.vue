<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useCodeText } from "../../composables/v1";
import type { JobStatusV1 } from "../../api/v1Resources";
import { isHeld, placeOfRow, type QueueRow } from "./queueRows";

/**
 * Bundle D2-A: the queue table. One row per logical job (key = root id). The order column shows
 * B7's own place (running / waiting N / unknown), never the row's index on screen. The stage
 * column says "not provided": B7 publishes no stage or progress. Rows select on click, Enter or
 * Space; the arrow keys move the focus (roving tabindex). Selecting only changes what is shown.
 */
const props = defineProps<{ rows: readonly QueueRow[]; selected: string | null }>();
const emit = defineEmits<{ select: [rootId: string] }>();
const { t, status } = useCodeText();

const body = ref<HTMLElement | null>(null);
const focused = ref<string | null>(null);
// Roving tabindex: one row is in the tab order (the focused one, else the selected one, else the first).
const tabRoot = computed(() => {
  const present = (root: string | null) => root !== null && props.rows.some((row) => row.rootId === root);
  if (present(focused.value)) return focused.value;
  if (present(props.selected)) return props.selected;
  return props.rows[0]?.rootId ?? null;
});

function move(index: number, delta: number): void {
  const next = props.rows[Math.min(props.rows.length - 1, Math.max(0, index + delta))];
  if (!next) return;
  focused.value = next.rootId;
  void nextTick(() => body.value?.querySelector<HTMLElement>(`tr[data-root="${next.rootId}"]`)?.focus());
}

function placeText(row: QueueRow): string {
  const place = placeOfRow(row);
  if (place.kind === "active") return t("queue.place.active");
  if (place.kind === "waiting") return t("queue.place.waiting", { position: place.position! });
  return t("queue.place.unknown");
}

const TONE: Partial<Record<JobStatusV1, string>> = { running: "primary", cancelling: "warning", held: "warning", waiting_user: "warning", blocked: "error" };
</script>

<template>
  <v-table density="compact" class="u-queue-table" role="grid" :aria-label="t('queue.tableLabel')" aria-readonly="true" data-testid="queue-table">
    <colgroup>
      <col class="u-queue-table__order" />
      <col />
      <col class="u-queue-table__status" />
      <col class="u-queue-table__stage" />
    </colgroup>
    <thead>
      <tr>
        <th scope="col">{{ t("queue.col.order") }}</th>
        <th scope="col">{{ t("queue.col.name") }}</th>
        <th scope="col">{{ t("queue.col.status") }}</th>
        <th scope="col">{{ t("queue.col.stage") }}</th>
      </tr>
    </thead>
    <tbody ref="body">
      <tr
        v-for="(row, index) in rows"
        :key="row.rootId"
        :class="{ 'u-queue-row': true, 'u-queue-row--selected': row.rootId === selected }"
        :tabindex="row.rootId === tabRoot ? 0 : -1"
        :aria-selected="row.rootId === selected ? 'true' : 'false'"
        data-testid="queue-row"
        :data-root="row.rootId"
        :data-job="row.current.id"
        :data-status="row.current.status"
        :data-place="placeOfRow(row).kind"
        :data-position="placeOfRow(row).position ?? undefined"
        :data-held="isHeld(row) ? 'true' : 'false'"
        :data-attempts="row.group.attempts.length"
        @click="emit('select', row.rootId)"
        @focus="focused = row.rootId"
        @keydown.enter.prevent="emit('select', row.rootId)"
        @keydown.space.prevent="emit('select', row.rootId)"
        @keydown.down.prevent="move(index, 1)"
        @keydown.up.prevent="move(index, -1)"
        @keydown.home.prevent="move(index, -rows.length)"
        @keydown.end.prevent="move(index, rows.length)"
      >
        <td data-testid="queue-row-place">
          <span class="u-queue-place">{{ placeText(row) }}</span>
          <v-chip v-if="isHeld(row)" size="x-small" label color="warning" variant="tonal" class="ml-1">{{ t("queue.place.held") }}</v-chip>
        </td>
        <td class="u-queue-table__name">
          <div class="u-ellipsis" :class="{ 'text-medium-emphasis': row.name === null }" :title="row.name ?? undefined" data-testid="queue-row-name">{{ row.name ?? t("queue.unknownName") }}</div>
          <div class="u-ellipsis text-caption" :title="row.current.id">{{ t("queue.rowMeta", { id: row.current.id, model: row.current.spec.modelId, profile: row.current.spec.profileId }) }}</div>
          <div v-if="row.group.attempts.length > 1 || row.group.open.length > 1" class="text-caption">
            <span>{{ t("queue.attempts", { count: row.group.attempts.length }) }}</span>
            <span v-if="row.group.open.length > 1" class="text-warning"> · {{ t("queue.branchCount", { count: row.group.open.length }) }}</span>
          </div>
        </td>
        <td data-testid="queue-row-status">
          <v-chip size="small" label variant="tonal" :color="TONE[row.current.status]" class="u-queue-chip" :title="status(row.current.status)">{{ status(row.current.status) }}</v-chip>
        </td>
        <td class="text-caption text-medium-emphasis" data-testid="queue-row-stage">{{ t("queue.stageUnavailable") }}</td>
      </tr>
    </tbody>
  </v-table>
</template>
