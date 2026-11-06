<script setup lang="ts">
import { mdiMagnify } from "@mdi/js";
import { computed } from "vue";
import JobStateChip from "../components/JobStateChip.vue";
import { useJobText } from "../components/useJobText";
import { usePreview } from "../composables";
import { HISTORY_FILTERS, type HistoryFilter, historyView, resultsView, type ResultsTab } from "../domain/demo";
import ResultDetail from "./ResultDetail.vue";

/**
 * Results & history (Expert), iteration 5 (#34). Two tabs over the SAME jobs, independent of the
 * queue's clean-up:
 *  - Results: succeeded jobs (they have outputs);
 *  - History: every finished job (succeeded, failed, cancelled), filterable by state.
 * Several jobs of one file are separate rows (job id and model on the second line, #33).
 * Viewer and re-export are S4 design items: shown disabled with the reason.
 */
const { demo, state, t } = usePreview();
const { jobLine } = useJobText();
const tab = computed(() => state.value.resultsTab);
const rows = computed(() => (tab.value === "results" ? resultsView(state.value) : historyView(state.value)));
const selected = computed(() => rows.value.find((j) => j.id === state.value.selectedResultId) ?? null);
const filters = computed(() => HISTORY_FILTERS.map((v) => ({ value: v, title: t(`history.filter.${v}`) })));
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="results-heading" data-testid="section-results" :data-tab="tab">
    <h1 id="results-heading" class="u-heading">{{ t("section.results") }}</h1>
    <v-btn-toggle
      :model-value="tab"
      mandatory
      divided
      variant="outlined"
      density="compact"
      color="primary"
      class="align-self-start"
      :aria-label="t('results.tabs')"
      data-testid="results-tabs"
      @update:model-value="(v: ResultsTab) => demo.setResultsTab(v)"
    >
      <v-btn value="results" data-testid="results-tab-results">{{ t("results.tab.results") }}</v-btn>
      <v-btn value="history" data-testid="results-tab-history">{{ t("results.tab.history") }}</v-btn>
    </v-btn-toggle>
    <div class="p-filterbar">
      <v-text-field
        :model-value="state.resultsQuery"
        :label="t('results.search')"
        :prepend-inner-icon="mdiMagnify"
        clearable
        hide-details
        class="p-filterbar__search"
        data-testid="results-search"
        @update:model-value="(v) => demo.setResultsQuery(v ?? '')"
      />
      <v-select v-if="tab === 'history'" :label="t('queue.filterLabel')" :items="filters" :model-value="state.historyFilter" class="p-filterbar__select" data-testid="history-filter" @update:model-value="(v) => demo.setHistoryFilter(v as HistoryFilter)" />
    </div>
    <div class="p-two-col">
      <v-card>
        <v-table density="compact" class="p-table" data-testid="results-table">
          <thead>
            <tr>
              <th>{{ t("inputs.col.name") }}</th>
              <th v-if="tab === 'history'" class="p-col-state">{{ t("progress.col.state") }}</th>
              <th class="p-col-format">{{ t("results.col.formats") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="rows.length === 0">
              <td :colspan="tab === 'history' ? 3 : 2" class="text-body-medium" data-testid="results-empty">{{ t(tab === "results" ? "result.empty" : "history.empty") }}</td>
            </tr>
            <tr
              v-for="job in rows"
              :key="job.id"
              tabindex="0"
              :class="{ 'p-row--selected': job.id === state.selectedResultId }"
              :aria-selected="job.id === state.selectedResultId"
              :data-testid="`result-row-${job.id}`"
              @click="demo.selectResult(job.id)"
              @keydown.enter.prevent="demo.selectResult(job.id)"
            >
              <td>
                <span class="p-ellipsis p-line" :title="job.inputName">{{ job.inputName }}</span>
                <span class="p-ellipsis p-line text-body-medium" :data-testid="`result-meta-${job.id}`">{{ jobLine(job) }}</span>
              </td>
              <td v-if="tab === 'history'"><JobStateChip :state="job.state" /></td>
              <td class="text-body-medium">{{ job.snapshot.formats.map((f) => f.toUpperCase()).join(" · ") }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card>
      <ResultDetail v-if="selected" :job="selected" />
      <v-alert v-else type="info" variant="outlined" data-testid="result-select-hint">{{ t("results.selectHint") }}</v-alert>
    </div>
  </section>
</template>
