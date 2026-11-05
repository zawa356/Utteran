<script setup lang="ts">
import { usePreview } from "../composables";
import JobStateChip from "./JobStateChip.vue";

/** The synthetic queue (order = list order). Selecting a row opens its detail below. */
const { demo, state, t } = usePreview();
</script>

<template>
  <v-list :aria-label="t('queue.listLabel')" data-testid="queue-list">
    <v-list-item
      v-for="(job, index) in state.jobs"
      :key="job.id"
      :active="job.id === state.selectedJobId"
      color="primary"
      :data-testid="`queue-row-${job.id}`"
      @click="demo.selectJob(job.id)"
    >
      <template #prepend>
        <span class="p-index text-caption">{{ index + 1 }}</span>
      </template>
      <template #title>
        <span class="p-ellipsis" :title="job.inputName">{{ job.inputName }}</span>
      </template>
      <template #subtitle>
        {{ job.stage ? t(`job.stage.${job.stage}`) : t("job.noStage") }}<template v-if="job.attempt > 1"> · {{ t("job.attempt", { n: job.attempt }) }}</template>
      </template>
      <template #append><JobStateChip :state="job.state" /></template>
    </v-list-item>
  </v-list>
</template>
