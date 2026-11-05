<script setup lang="ts">
import { mdiFileDocumentOutline, mdiFolderOpenOutline, mdiMagnify, mdiRefresh, mdiTextBoxSearchOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { outputNames, resultsView } from "../domain/demo";
import { useSnapshotText } from "../components/useSnapshotText";

/**
 * Results & history (Expert): FINISHED outputs only — search, open one, see its outputs and the
 * settings it was made with. Viewer and re-export are S4 design items: shown disabled with the
 * reason, never as working buttons.
 */
const { demo, state, t } = usePreview();
const { snapshotText } = useSnapshotText();
const rows = computed(() => resultsView(state.value));
const selected = computed(() => rows.value.find((j) => j.id === state.value.selectedResultId) ?? null);
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="results-heading" data-testid="section-results">
    <h1 id="results-heading" class="u-heading">{{ t("section.results") }}</h1>
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
    </div>
    <div class="p-two-col">
      <v-card>
        <v-table density="compact" class="p-table" data-testid="results-table">
          <thead>
            <tr>
              <th>{{ t("inputs.col.name") }}</th>
              <th class="p-col-format">{{ t("results.col.formats") }}</th>
              <th class="p-col-index text-end">{{ t("results.col.attempt") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="rows.length === 0"><td colspan="3" class="text-body-medium" data-testid="results-empty">{{ t("result.empty") }}</td></tr>
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
              <td><span class="p-ellipsis" :title="job.inputName">{{ job.inputName }}</span></td>
              <td class="text-body-medium">{{ job.snapshot.formats.map((f) => f.toUpperCase()).join(" · ") }}</td>
              <td class="text-end">{{ job.attempt }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card>
      <v-card v-if="selected" class="pa-3 u-stack" data-testid="result-detail">
        <h2 class="u-subheading p-ellipsis" :title="selected.inputName">{{ selected.inputName }}</h2>
        <ul class="p-outputs">
          <li v-for="name in outputNames(selected)" :key="name"><v-icon :icon="mdiFileDocumentOutline" size="small" aria-hidden="true" /> <span class="p-ellipsis" :title="name">{{ name }}</span></li>
        </ul>
        <dl class="p-dl text-body-medium">
          <dt>{{ t("job.snapshot") }}</dt>
          <dd data-testid="result-snapshot">{{ snapshotText(selected.snapshot) }}</dd>
        </dl>
        <div class="u-row">
          <v-btn variant="tonal" :prepend-icon="mdiTextBoxSearchOutline" disabled>{{ t("results.viewer") }}</v-btn>
          <v-btn variant="tonal" :prepend-icon="mdiRefresh" disabled>{{ t("results.reexport") }}</v-btn>
          <v-btn variant="tonal" :prepend-icon="mdiFolderOpenOutline" disabled>{{ t("result.folder") }}</v-btn>
        </div>
        <p class="text-body-small" data-testid="results-later">{{ t("results.later") }}</p>
      </v-card>
      <v-alert v-else type="info" variant="outlined" data-testid="result-select-hint">{{ t("results.selectHint") }}</v-alert>
    </div>
  </section>
</template>
