<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";

/** Results & history sketch (S4): synthetic finished jobs in a standard data table. */
const { state, t } = usePreview();
const headers = computed(() => [
  { title: t("inputs.col.name"), key: "name" },
  { title: t("results.col.formats"), key: "formats" },
  { title: t("results.col.attempt"), key: "attempt", align: "end" as const },
]);
const items = computed(() =>
  state.value.jobs
    .filter((j) => j.state === "succeeded")
    .map((j) => ({ id: j.id, name: j.inputName, formats: j.snapshot.formats.map((f) => f.toUpperCase()).join(" · "), attempt: j.attempt })),
);
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="results-heading" data-testid="section-results">
    <h1 id="results-heading" class="u-heading">{{ t("section.results") }}</h1>
    <v-alert type="info" variant="outlined">{{ t("results.sketch") }}</v-alert>
    <v-data-table :headers="headers" :items="items" item-value="id" density="compact" :no-data-text="t('result.empty')" hide-default-footer data-testid="results-table" />
  </section>
</template>
