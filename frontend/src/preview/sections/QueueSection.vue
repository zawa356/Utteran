<script setup lang="ts">
import AreaState from "../components/AreaState.vue";
import JobDetail from "../components/JobDetail.vue";
import QueueList from "../components/QueueList.vue";
import QueueToolbar from "../components/QueueToolbar.vue";
import SyncStatus from "../components/SyncStatus.vue";
import { usePreview } from "../composables";

/** The queue as its own page: the same list/detail parts as the Expert right pane. */
const { demo, state, t } = usePreview();
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="queue-heading" data-testid="section-queue">
    <h1 id="queue-heading" class="u-heading">{{ t("section.queue") }}</h1>
    <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('queue.empty')" testid="queue-section" @retry="demo.recoverArea('queue')">
      <QueueToolbar />
      <div class="p-two-col">
        <v-card class="pa-2"><QueueList /></v-card>
        <JobDetail />
      </div>
    </AreaState>
    <SyncStatus />
  </section>
</template>
