<script setup lang="ts">
import { mdiMagnify, mdiRefresh } from "@mdi/js";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import QueueDetail from "./QueueDetail.vue";
import { buildQueueRows, QUEUE_FILTERS, QUEUE_SORTS, type QueueFilter, type QueueSort, reconcileSelection, viewRows } from "./queueRows";
import QueueTable from "./QueueTable.vue";

/**
 * Bundle D2-A (`--next-ui-v1`, Expert): the queue page. It reads the one board controller
 * (`core.v1.board`; no other client, list or cache) and shows its queue as a table plus the
 * selected logical job's detail. Search, filter and sort are display-only and local; they never
 * change B7's order and never send anything. Operations stay in Expert ③ (D2-B moves them).
 */
const core = useCore();
const board = core.v1!.board;
const job = core.v1!.job;
const s = useStore(board.store);
const inputs = useStore(core.inputs.store);
const { t } = useCodeText();

// One lease on the board's polling while this page is shown (Expert ③ holds its own; one timer).
let release: (() => void) | null = null;
onMounted(() => (release = board.retainWatch()));
onUnmounted(() => release?.());

// Basenames known in this session only (the API carries no path; nothing is asked of the server).
const names = computed(() => new Map(inputs.value.rows.map((row) => [job.fileIdFor(row.clientFileId), fileName(row.inputPath)])));
const all = computed(() => {
  void s.value;
  return buildQueueRows(
    s.value.queue,
    (group) => board.current(group),
    (jobId) => board.placeOf(jobId),
    (clientFileId) => names.value.get(clientFileId) ?? null,
  );
});

// Display state of this page only (not persisted, not shared).
const draft = ref("");
const query = ref("");
const composing = ref(false);
const filter = ref<QueueFilter>("all");
const sort = ref<QueueSort>("queue");
function onSearch(value: string | null): void {
  draft.value = value ?? "";
  // While an IME composes, nothing is applied (the text is not final yet).
  if (!composing.value) query.value = draft.value;
}
function onCompositionEnd(): void {
  composing.value = false;
  query.value = draft.value;
}
const filters = computed(() => QUEUE_FILTERS.map((value) => ({ value, title: t(`queue.filter.${value}`) })));
const sorts = computed(() => QUEUE_SORTS.map((value) => ({ value, title: t(`queue.sort.${value}`) })));
const shown = computed(() => viewRows(all.value, { query: query.value, filter: filter.value, sort: sort.value }));

// Selection by logical job (root id). The detail always shows the row object of the latest read.
const selected = ref<string | null>(null);
const lost = ref(false);
watch(
  () => [s.value.phase, all.value] as const,
  ([phase, rows]) => {
    const next = reconcileSelection(selected.value, phase, rows);
    if (next.lost) lost.value = true;
    selected.value = next.selected;
  },
);
function select(rootId: string): void {
  selected.value = rootId;
  lost.value = false;
}
// Nothing from an earlier answer is shown while the board is not in a completed read.
const detailRow = computed(() => (s.value.phase === "done" ? (all.value.find((row) => row.rootId === selected.value) ?? null) : null));
const filteredOut = computed(() => detailRow.value !== null && !shown.value.some((row) => row.rootId === detailRow.value!.rootId));

const loading = computed(() => s.value.phase === "idle" || s.value.phase === "pending");
const openAction = computed(() => (s.value.action?.phase === "pending" || s.value.action?.phase === "unknown" ? s.value.action.phase : null));
</script>

<template>
  <section class="u-page u-page--wide u-stack u-queue" aria-labelledby="queue-heading" data-testid="section-queue" :data-phase="s.phase" :data-count="all.length" :data-shown="shown.length">
    <h1 id="queue-heading" class="u-heading">{{ t("section.queue") }}</h1>
    <p class="text-body-2">{{ t("queue.explain") }}</p>
    <ul class="u-notes text-caption">
      <li data-testid="queue-order-note">{{ t("queue.orderNote") }}</li>
      <li data-testid="queue-stage-note">{{ t("queue.stageNote") }}</li>
      <li data-testid="queue-ops-note">{{ t("queue.opsNote") }}</li>
    </ul>

    <v-alert v-if="openAction" type="warning" density="compact" role="status" data-testid="queue-action-open" :data-action-phase="openAction">
      {{ t(openAction === "unknown" ? "queue.action.unknown" : "queue.action.pending") }}
      <template #append>
        <v-btn variant="text" size="small" data-testid="queue-open-transcribe" @click="core.shell.navigate('transcribe')">{{ t("queue.openTranscribe") }}</v-btn>
      </template>
    </v-alert>
    <v-alert v-if="s.phase === 'done' && s.queueView?.dispatch === 'paused'" type="info" density="compact" data-testid="queue-dispatch-paused">{{ t("queue.paused") }}</v-alert>

    <div class="u-queue-toolbar" role="search">
      <v-text-field
        :model-value="draft"
        :label="t('queue.search')"
        :prepend-inner-icon="mdiMagnify"
        density="compact"
        clearable
        hide-details
        class="u-queue-toolbar__search"
        data-testid="queue-search"
        @update:model-value="onSearch"
        @compositionstart="composing = true"
        @compositionend="onCompositionEnd"
      />
      <v-select v-model="filter" :items="filters" :label="t('queue.filterLabel')" density="compact" hide-details class="u-queue-toolbar__select" data-testid="queue-filter" />
      <v-select v-model="sort" :items="sorts" :label="t('queue.sortLabel')" density="compact" hide-details class="u-queue-toolbar__select" data-testid="queue-sort" />
      <v-btn variant="outlined" :prepend-icon="mdiRefresh" :disabled="loading && s.phase === 'pending'" data-testid="queue-reload" @click="board.refresh()">{{ t("queue.reload") }}</v-btn>
    </div>
    <p v-if="s.phase === 'done' && all.length > 0" class="text-caption" role="status" data-testid="queue-count">
      {{ t("queue.count", { shown: shown.length, total: all.length }) }}<template v-if="sort !== 'queue'"> · <span data-testid="queue-sort-note">{{ t("queue.sortNote") }}</span></template>
    </p>

    <div v-if="s.phase === 'error' && s.error" data-testid="queue-error" :data-error-kind="s.error.kind" :data-error-code="s.error.code ?? undefined">
      <ErrorAlert :error="s.error" :title="t('queue.errorTitle')" :retry-label="t('queue.reload')" testid="queue-error-alert" @retry="board.refresh()" />
    </div>
    <p v-else-if="loading" class="text-body-2" role="status" data-testid="queue-loading">{{ t("queue.loading") }}</p>
    <div v-else class="u-queue-grid">
      <div class="u-queue-grid__list">
        <p v-if="all.length === 0" class="text-body-2" data-testid="queue-empty">{{ t("queue.empty") }}</p>
        <p v-else-if="shown.length === 0" class="text-body-2" data-testid="queue-no-match">{{ t("queue.noMatch", { count: all.length }) }}</p>
        <v-card v-else variant="outlined">
          <QueueTable :rows="shown" :selected="selected" @select="select" />
        </v-card>
      </div>
      <QueueDetail :row="detailRow" :lost="lost" :filtered="filteredOut" />
    </div>
  </section>
</template>
