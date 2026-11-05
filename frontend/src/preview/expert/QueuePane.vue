<script setup lang="ts">
import { mdiArrowRight, mdiClose, mdiPause, mdiPlay, mdiReplay, mdiSendOutline, mdiStepForward } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobStateChip from "../components/JobStateChip.vue";
import { usePreview } from "../composables";
import { isActive, submittedInputs } from "../domain/demo";

/**
 * Right pane of the work area: ONLY submission and a summary of what is running now.
 * Full queue management (search/filter/sort/reorder/detail) is the Queue page; finished outputs
 * are the Results page. This pane links to both instead of repeating their lists.
 */
const { demo, layout, state, t } = usePreview();
const open = computed(() => {
  const submitted = submittedInputs(state.value);
  return state.value.inputs.filter((i) => i.supported && !submitted.has(i.id));
});
const openSelected = computed(() => open.value.filter((i) => state.value.selected.includes(i.id)));
const counts = computed(() => {
  const jobs = state.value.jobs;
  return {
    waiting: jobs.filter((j) => j.state === "waiting").length,
    running: jobs.filter((j) => j.state === "preparing" || j.state === "running").length,
    failed: jobs.filter((j) => j.state === "failed" || j.state === "cancelled").length,
    done: jobs.filter((j) => j.state === "succeeded").length,
  };
});
// Now running + needing attention (failed/cancelled), at most 6 rows.
const current = computed(() => state.value.jobs.filter((j) => j.state === "preparing" || j.state === "running" || j.state === "failed" || j.state === "cancelled").slice(0, 6));
</script>

<template>
  <div class="p-pane__body">
    <div class="p-pane__title">
      <h2 class="u-subheading">{{ t("expert.queue") }}</h2>
    </div>
    <div class="u-stack">
      <v-btn
        color="primary"
        block
        :prepend-icon="mdiSendOutline"
        :disabled="openSelected.length === 0 || state.inputsArea !== 'ready'"
        data-testid="submit-selected"
        @click="demo.submit(openSelected.map((i) => i.id))"
      >
        {{ t("queue.submitSelected", { count: openSelected.length }) }}
      </v-btn>
      <v-btn block variant="outlined" :disabled="open.length === 0 || state.inputsArea !== 'ready'" data-testid="submit-all" @click="demo.submit(open.map((i) => i.id))">
        {{ t("queue.submitAll", { count: open.length }) }}
      </v-btn>
      <p class="text-body-small" data-testid="submit-demo-note">{{ t("start.demoNoteShort") }}</p>
    </div>
    <v-divider />
    <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('queue.empty')" testid="expert-queue" @retry="demo.recoverArea('queue')">
      <dl class="p-counts" data-testid="queue-counts">
        <div><dt>{{ t("queue.count.waiting") }}</dt><dd data-testid="count-waiting">{{ counts.waiting }}</dd></div>
        <div><dt>{{ t("queue.count.running") }}</dt><dd data-testid="count-running">{{ counts.running }}</dd></div>
        <div><dt>{{ t("queue.count.attention") }}</dt><dd data-testid="count-attention">{{ counts.failed }}</dd></div>
        <div><dt>{{ t("queue.count.done") }}</dt><dd data-testid="count-done">{{ counts.done }}</dd></div>
      </dl>
      <div class="u-row">
        <v-chip size="small" label :color="state.queuePaused ? 'warning' : undefined" variant="tonal" :prepend-icon="state.queuePaused ? mdiPause : mdiPlay" data-testid="queue-paused">
          {{ state.queuePaused ? t("queue.paused") : t("queue.running") }}
        </v-chip>
        <v-btn size="small" variant="text" :prepend-icon="state.queuePaused ? mdiPlay : mdiPause" data-testid="queue-pause" @click="demo.togglePause()">
          {{ state.queuePaused ? t("queue.resume") : t("queue.pause") }}
        </v-btn>
      </div>
      <h3 class="text-body-medium font-weight-medium">{{ t("queue.now") }}</h3>
      <p v-if="current.length === 0" class="text-body-small" data-testid="queue-now-empty">{{ t("queue.nowEmpty") }}</p>
      <ul v-else class="p-now" data-testid="queue-now">
        <li v-for="job in current" :key="job.id" :data-testid="`now-${job.id}`">
          <span class="p-ellipsis text-body-medium" :title="job.inputName">{{ job.inputName }}</span>
          <JobStateChip :state="job.state" size="x-small" />
          <v-btn v-if="isActive(job)" size="x-small" variant="text" :icon="mdiClose" :aria-label="t('job.cancelNamed', { name: job.inputName })" :title="t('job.cancel')" @click="demo.cancel(job.id)" />
          <v-btn v-else size="x-small" variant="text" color="primary" :icon="mdiReplay" :aria-label="t('job.retryNamed', { name: job.inputName })" :title="t('job.retry')" :data-testid="`now-retry-${job.id}`" @click="demo.retry(job.id)" />
        </li>
      </ul>
      <div class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
        <v-chip size="x-small" label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
        <v-btn size="small" variant="outlined" :prepend-icon="mdiStepForward" :disabled="counts.waiting + counts.running === 0" data-testid="queue-step-all" @click="demo.stepAll()">{{ t("demo.stepAll") }}</v-btn>
      </div>
    </AreaState>
    <v-divider />
    <div class="p-links">
      <v-btn size="small" variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="open-queue-page" @click="layout.navigate('queue')">{{ t("queue.openPage", { count: state.jobs.length }) }}</v-btn>
      <v-btn size="small" variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="open-results-page" @click="layout.navigate('results')">{{ t("results.openPage", { count: counts.done }) }}</v-btn>
    </div>
  </div>
</template>
