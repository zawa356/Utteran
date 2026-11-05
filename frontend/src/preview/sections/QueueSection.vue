<script setup lang="ts">
import { mdiArrowDown, mdiArrowUp, mdiBroom, mdiMagnify, mdiPause, mdiPlay, mdiStepForward } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobDetail from "../components/JobDetail.vue";
import JobStateChip from "../components/JobStateChip.vue";
import JobStatusNotice from "../components/JobStatusNotice.vue";
import { usePreview } from "../composables";
import { isActive, type QueueFilter, type QueueSort, queueView } from "../domain/demo";

/**
 * Queue page (Expert): management of ALL jobs — search, filter, sort, reorder, pause, clean up,
 * and the selected job's detail. The work area only summarises; this page does not repeat the
 * inputs or the results.
 */
const { demo, state, t } = usePreview();
const rows = computed(() => queueView(state.value));
const filters = computed(() => (["all", "active", "attention", "finished"] as const).map((v) => ({ value: v, title: t(`queue.filter.${v}`) })));
const sorts = computed(() => (["order", "name", "state"] as const).map((v) => ({ value: v, title: t(`queue.sort.${v}`) })));
const finished = computed(() => state.value.jobs.filter((j) => !isActive(j)).length);
const active = computed(() => state.value.jobs.filter(isActive).length);
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="queue-heading" data-testid="section-queue">
    <h1 id="queue-heading" class="u-heading">{{ t("section.queue") }}</h1>
    <JobStatusNotice :active="active > 0" />
    <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('queue.empty')" testid="queue-section" @retry="demo.recoverArea('queue')">
      <div class="p-filterbar">
        <v-text-field
          :model-value="state.queueQuery"
          :label="t('queue.search')"
          :prepend-inner-icon="mdiMagnify"
          clearable
          hide-details
          class="p-filterbar__search"
          data-testid="queue-search"
          @update:model-value="(v) => demo.setQueueQuery(v ?? '')"
        />
        <v-select :label="t('queue.filterLabel')" :items="filters" :model-value="state.queueFilter" class="p-filterbar__select" data-testid="queue-filter" @update:model-value="(v) => demo.setQueueFilter(v as QueueFilter)" />
        <v-select :label="t('queue.sortLabel')" :items="sorts" :model-value="state.queueSort" class="p-filterbar__select" data-testid="queue-sort" @update:model-value="(v) => demo.setQueueSort(v as QueueSort)" />
        <v-spacer />
        <v-btn variant="tonal" :prepend-icon="state.queuePaused ? mdiPlay : mdiPause" data-testid="queue-page-pause" @click="demo.togglePause()">
          {{ state.queuePaused ? t("queue.resume") : t("queue.pause") }}
        </v-btn>
        <v-btn variant="text" :prepend-icon="mdiBroom" :disabled="finished === 0" data-testid="queue-clear" @click="demo.clearFinished()">{{ t("queue.clearFinished") }}</v-btn>
        <v-btn variant="outlined" :prepend-icon="mdiStepForward" :disabled="active === 0" data-testid="queue-page-step-all" @click="demo.stepAll()">{{ t("demo.stepAll") }}</v-btn>
      </div>
      <div class="p-two-col">
        <v-card>
          <v-table density="compact" class="p-table" data-testid="queue-table">
            <thead>
              <tr>
                <th class="p-col-index">#</th>
                <th>{{ t("inputs.col.name") }}</th>
                <th class="p-col-state">{{ t("progress.col.state") }}</th>
                <th class="p-col-stage">{{ t("progress.col.stage") }}</th>
                <th class="p-col-order"><span class="p-sr-only">{{ t("queue.col.order") }}</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="rows.length === 0"><td colspan="5" class="text-body-medium" data-testid="queue-no-match">{{ t("queue.noMatch") }}</td></tr>
              <tr
                v-for="{ job, position } in rows"
                :key="job.id"
                :class="{ 'p-row--selected': job.id === state.selectedJobId }"
                :aria-selected="job.id === state.selectedJobId"
                tabindex="0"
                :data-testid="`queue-row-${job.id}`"
                @click="demo.selectJob(job.id)"
                @keydown.enter.prevent="demo.selectJob(job.id)"
              >
                <td class="text-body-medium">{{ position }}</td>
                <td><span class="p-ellipsis" :title="job.inputName">{{ job.inputName }}</span></td>
                <td><JobStateChip :state="job.state" /></td>
                <td class="text-body-medium">{{ job.stage ? t(`job.stage.${job.stage}`) : t("job.noStage") }}<template v-if="job.attempt > 1"> · {{ t("job.attempt", { n: job.attempt }) }}</template></td>
                <td class="text-end">
                  <template v-if="job.state === 'waiting' && state.queueSort === 'order'">
                    <v-btn size="small" variant="text" :icon="mdiArrowUp" :aria-label="t('job.upNamed', { name: job.inputName })" @click.stop="demo.move(job.id, -1)" />
                    <v-btn size="small" variant="text" :icon="mdiArrowDown" :aria-label="t('job.downNamed', { name: job.inputName })" @click.stop="demo.move(job.id, 1)" />
                  </template>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card>
        <JobDetail />
      </div>
    </AreaState>
  </section>
</template>
