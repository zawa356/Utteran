<script setup lang="ts">
import { mdiMagnify } from "@mdi/js";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import type { JobV1 } from "../../api/v1Resources";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { MessageKey } from "../../i18n";
import ResultDetail from "./ResultDetail.vue";

const { v1, inputs } = useCore();
const { board, job, deletion, queueGate } = v1!;
const state = useStore(board.store);
const inputState = useStore(inputs.store);
const gate = useStore(queueGate.store);
const deletionState = useStore(deletion.store);
const { t, status, reason } = useCodeText();

let release: (() => void) | undefined;
onMounted(() => { release = board.retainWatch(); });
onUnmounted(() => {
  release?.();
  closeUnsentPlan();
});

const tab = ref<"results" | "history">("results");
const selected = ref<string | null>(null);
const filter = ref("all");
const sort = ref<"newest" | "oldest">("newest");
const query = ref("");
const composing = ref(false);
const reload = ref<{ $el: HTMLElement } | null>(null);
const refreshing = ref(false);
const filters = computed(() => ["all", "succeeded", "failed", "cancelled", "interrupted"].map(value => ({
  value, title: value === "all" ? t("results.all") : status(value),
})));
const sorts = computed(() => (["newest", "oldest"] as const).map(value => ({
  value, title: t(`results.${value}`),
})));
const names = computed(() => new Map(inputState.value.rows.map(row => [job.fileIdFor(row.clientFileId), fileName(row.inputPath)])));
const basename = (item: JobV1) => names.value.get(item.clientFileId) ?? null;
const label = (item: JobV1) => basename(item) ?? t("results.unknownFile");

const hidden = computed(() => tab.value === "history" && state.value.showHidden && state.value.visibilitySupported);
const source = computed(() => tab.value === "results"
  ? state.value.history.filter(group => group.open.length === 0 && group.latest.status === "succeeded")
  : hidden.value ? state.value.hidden : state.value.history);
const rows = computed(() => {
  const needle = query.value.trim().toLowerCase();
  const items = tab.value === "results" ? source.value : source.value.filter(group =>
    (filter.value === "all" || group.latest.status === filter.value) &&
    (!needle || group.rootId.toLowerCase().includes(needle) || group.attempts.some(attempt =>
      attempt.id.toLowerCase().includes(needle) || basename(attempt)?.toLowerCase().includes(needle))));
  return [...items].sort((a, b) => {
    const delta = Date.parse(b.latest.createdAt) - Date.parse(a.latest.createdAt);
    return tab.value === "history" && sort.value === "oldest" ? -delta : delta;
  });
});
const loading = computed(() => refreshing.value || state.value.phase === "idle" || state.value.phase === "pending");
const detail = computed(() => state.value.phase === "done" && !loading.value
  ? rows.value.find(group => group.rootId === selected.value) ?? null : null);
const busy = computed(() => gate.value.owner !== null || deletionState.value.phase !== "idle");
const listId = computed(() => tab.value === "results" ? "results-table-results" : hidden.value ? "v1-board-hidden" : "v1-board-history");

function closeUnsentPlan(): void {
  if (["planning", "planned", "plan_error"].includes(deletion.store.get().phase)) deletion.close();
}

watch([tab, hidden], () => { selected.value = null; composing.value = false; closeUnsentPlan(); });
watch([rows, () => state.value.phase], async ([items, phase]) => {
  if (selected.value === null || (phase !== "error" && items.some(row => row.rootId === selected.value))) return;
  const restoreFocus = document.activeElement?.closest("[data-testid='results-detail'], [data-testid='results-table']");
  selected.value = null;
  closeUnsentPlan();
  if (restoreFocus) {
    await nextTick();
    reload.value?.$el.focus();
  }
});

async function read(showHidden?: boolean): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    if (showHidden === undefined) await board.refresh();
    else await board.setShowHidden(showHidden);
  } finally {
    refreshing.value = false;
  }
}
</script>

<template>
  <section class="u-page u-page--wide u-stack results-page" aria-labelledby="results-heading" data-testid="section-results" :data-tab="tab" :data-phase="state.phase">
    <h1 id="results-heading" class="u-heading">{{ t("section.results") }}</h1>
    <v-tabs v-model="tab" :aria-label="t('results.tabs')" density="compact" color="primary">
      <v-tab id="tab-results" value="results" aria-controls="results-panel" data-testid="results-tab-results">{{ t("section.results") }}</v-tab>
      <v-tab id="tab-history" value="history" aria-controls="results-panel" data-testid="results-tab-history">{{ t("v1.board.history") }}</v-tab>
    </v-tabs>
    <div id="results-panel" role="tabpanel" :aria-labelledby="`tab-${tab}`" class="u-stack">
      <div class="u-row results-filters">
        <template v-if="tab === 'history'">
          <v-text-field
            :model-value="query" :label="t('results.search')" :prepend-inner-icon="mdiMagnify"
            density="compact" hide-details clearable data-testid="history-search"
            @update:model-value="value => { if (!composing) query = value ?? ''; }"
            @compositionstart="composing = true"
            @compositionend="(event: CompositionEvent) => { composing = false; query = (event.target as HTMLInputElement).value; }"
          />
          <v-select v-model="filter" :items="filters" :label="t('results.status')" density="compact" hide-details data-testid="history-filter" />
          <v-select v-model="sort" :items="sorts" :label="t('results.sort')" density="compact" hide-details data-testid="history-sort" />
          <v-switch
            v-if="state.visibilitySupported" :model-value="hidden" :label="t('results.hiddenOnly')"
            color="primary" density="compact" hide-details :disabled="refreshing" data-testid="v1-board-show-hidden"
            @update:model-value="value => read(value === true)"
          />
        </template>
        <v-btn ref="reload" variant="text" :loading="refreshing" size="small" data-testid="v1-board-refresh" @click="read()">{{ t("v1.board.refresh") }}</v-btn>
      </div>

      <v-alert
        v-if="state.action" :type="state.action.phase === 'done' ? 'success' : state.action.phase === 'pending' ? 'info' : 'warning'"
        density="compact" role="status" data-testid="v1-board-action" :data-kind="state.action.kind" :data-action-phase="state.action.phase"
      >
        {{ t(`v1.board.action.${state.action.phase}` as MessageKey) }}
        <span v-if="state.action.error?.code">({{ reason(state.action.error.code) }})</span>
        <div class="u-row">
          <v-btn v-if="state.action.phase === 'unknown'" variant="text" size="small" data-testid="v1-board-recheck" @click="board.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
          <v-btn v-else-if="state.action.phase !== 'pending'" variant="text" size="small" data-testid="v1-board-action-close" @click="board.dismissAction()">{{ t("v1.board.action.close") }}</v-btn>
        </div>
      </v-alert>
      <p v-if="loading" role="status">{{ t("results.loading") }}</p>
      <ErrorAlert v-else-if="state.error" :error="state.error" testid="v1-board-error" :data-error-kind="state.error.kind" @retry="read()" />
      <div v-else class="results-columns">
        <div :data-testid="listId">
          <v-table density="compact" class="results-table" data-testid="results-table">
            <thead><tr><th scope="col">{{ t("results.job") }}</th><th scope="col">{{ t("results.status") }}</th></tr></thead>
            <tbody>
              <tr v-if="rows.length === 0">
                <td colspan="2" role="status" data-testid="results-empty">{{ t(source.length ? "results.noMatch" : hidden ? "v1.board.emptyHidden" : tab === "history" ? "v1.board.emptyHistory" : "results.empty") }}</td>
              </tr>
              <tr
                v-for="group in rows" :key="group.rootId" data-testid="v1-board-row" :data-root="group.rootId"
                :data-job="group.latest.id" :data-status="group.latest.status" :data-attempts="group.attempts.length"
              >
                <td class="py-2">
                  <v-btn
                    block variant="text" class="results-select" :color="selected === group.rootId ? 'primary' : undefined"
                    :aria-pressed="selected === group.rootId" data-testid="results-select" @click="selected = group.rootId"
                  >
                    <span class="text-left u-break">
                      <span class="d-block" data-testid="v1-board-row-title">{{ label(group.latest) }}</span>
                      <span class="d-block text-caption">{{ group.latest.id }}</span>
                    </span>
                  </v-btn>
                </td>
                <td class="u-break">{{ status(group.latest.status) }}</td>
              </tr>
            </tbody>
          </v-table>
        </div>
        <ResultDetail v-if="detail" :group="detail" :label="label(detail.latest)" :history="tab === 'history'" :hidden="hidden" :busy="busy" />
        <p v-else-if="rows.length" class="text-body-2">{{ t("results.select") }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.results-page { container-type: inline-size; }
.results-columns { display: grid; align-items: start; gap: 16px; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); }
.results-filters :deep(.v-input) { flex: 1 1 155px; min-width: 0; }
.results-table :deep(table) { table-layout: fixed; }
.results-table th:first-child { width: 72%; }
.results-select { justify-content: start; height: auto; padding: 6px 8px; text-transform: none; letter-spacing: normal; }
.results-select :deep(.v-btn__content) { white-space: normal; min-width: 0; }
@container (max-width: 630px) { .results-columns { grid-template-columns: minmax(0, 1fr); } }
</style>
