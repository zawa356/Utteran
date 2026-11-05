<script setup lang="ts">
import { mdiAlertOutline, mdiArrowRight, mdiCheckCircleOutline, mdiClose, mdiPause, mdiPlay, mdiReplay, mdiSendOutline, mdiStepForward } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobProgress from "../components/JobProgress.vue";
import JobStateChip from "../components/JobStateChip.vue";
import JobStatusNotice from "../components/JobStatusNotice.vue";
import PaneHeading from "../components/PaneHeading.vue";
import { usePreview } from "../composables";
import { isActive, miniQueue, skipReason, submitBlock } from "../domain/demo";

/**
 * Step 3 (right pane), split in two (iteration 3, owner memos #9/#10):
 *  - top: "submit the selection" with its count, why it cannot be submitted, and what the last
 *    submit did (added n / skipped and why) — so the result is visible without leaving the page;
 *  - bottom: a SHORT queue (≤ 5 rows: just added, running, needing attention, …) that shows the new
 *    jobs at once, and "Open queue" for the full management page. No counters, no duplicate of
 *    the Queue page's tools.
 */
const { demo, layout, state, t } = usePreview();
const MINI = 5;
const open = computed(() => state.value.inputs.filter((i) => skipReason(state.value, i) === null));
const openSelected = computed(() => open.value.filter((i) => state.value.selected.includes(i.id)));
const block = computed(() => submitBlock(state.value));
const skippedBefore = computed(() => state.value.selected.length - openSelected.value.length);
const rows = computed(() => miniQueue(state.value, MINI));
const recent = computed(() => new Set(state.value.lastSubmit?.jobIds ?? []));
const anyActive = computed(() => state.value.jobs.some(isActive));
const hidden = computed(() => Math.max(0, state.value.jobs.length - rows.value.length));
const result = computed(() => {
  const r = state.value.lastSubmit;
  if (!r) return null;
  const parts: string[] = [];
  if (r.created > 0) parts.push(t("submit.added", { count: r.created }));
  if (r.alreadySubmitted > 0) parts.push(t("submit.skippedSubmitted", { count: r.alreadySubmitted }));
  if (r.unsupported > 0) parts.push(t("submit.skippedUnsupported", { count: r.unsupported }));
  if (r.invalid > 0) parts.push(t("submit.skippedInvalid", { count: r.invalid }));
  if (r.diarization > 0) parts.push(t("submit.skippedDiarization", { count: r.diarization }));
  return { ok: r.created > 0, text: parts.join(" ") || t("submit.nothing") };
});
</script>

<template>
  <div class="p-pane__body p-pane__body--split">
    <PaneHeading id="pane-queue-heading" :step="3" :title="t('expert.queue')" />
    <div class="p-split">
      <!-- top: submit -->
      <div class="u-stack" data-testid="submit-area">
        <v-btn
          color="primary"
          size="large"
          block
          :prepend-icon="mdiSendOutline"
          :disabled="block !== null"
          data-testid="submit-selected"
          @click="demo.submit(state.selected)"
        >
          {{ t("queue.submitSelected", { count: openSelected.length }) }}
        </v-btn>
        <div v-if="result" class="p-feedback" role="status" aria-live="polite" data-testid="submit-result">
          <v-icon :icon="result.ok ? mdiCheckCircleOutline : mdiAlertOutline" :color="result.ok ? 'success' : 'warning'" size="small" aria-hidden="true" />
          <span>{{ result.text }}</span>
        </div>
        <div v-else-if="block && block !== 'area'" class="p-feedback" data-testid="submit-blocked">
          <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
          <span>{{ t(`submit.block.${block}`) }}</span>
        </div>
        <div v-else-if="skippedBefore > 0" class="p-feedback" data-testid="submit-partial">
          <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
          <span>{{ t("submit.partial", { count: skippedBefore }) }}</span>
        </div>
        <v-btn variant="text" color="primary" :disabled="open.length === 0 || state.inputsArea !== 'ready'" data-testid="submit-all" @click="demo.submit(open.map((i) => i.id))">
          {{ t("queue.submitAll", { count: open.length }) }}
        </v-btn>
      </div>

      <!-- bottom: short queue -->
      <section class="p-split__bottom" aria-labelledby="mini-queue-heading" data-testid="mini-queue">
        <div class="u-row">
          <h3 id="mini-queue-heading" class="text-body-large font-weight-medium">{{ t("queue.mini") }}</h3>
          <span class="text-body-medium" data-testid="mini-total">{{ t("queue.total", { count: state.jobs.length }) }}</span>
          <v-spacer />
          <v-btn
            v-if="state.jobs.length > 0"
            size="small"
            variant="text"
            :icon="state.queuePaused ? mdiPlay : mdiPause"
            :aria-label="state.queuePaused ? t('queue.resume') : t('queue.pause')"
            :title="state.queuePaused ? t('queue.resume') : t('queue.pause')"
            data-testid="queue-pause"
            @click="demo.togglePause()"
          />
        </div>
        <v-chip v-if="state.queuePaused" label color="warning" variant="tonal" :prepend-icon="mdiPause" data-testid="queue-paused">{{ t("queue.paused") }}</v-chip>
        <JobStatusNotice :active="anyActive" />
        <div class="p-split__scroll">
          <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('queue.empty')" testid="expert-queue" @retry="demo.recoverArea('queue')">
            <ul class="p-mini" data-testid="queue-now">
              <li v-for="job in rows" :key="job.id" :class="{ 'p-mini__new': recent.has(job.id) }" :data-testid="`now-${job.id}`" :data-new="recent.has(job.id)">
                <span class="p-ellipsis p-mini__name" :title="job.inputName">{{ job.inputName }}</span>
                <JobStateChip :state="job.state" />
                <v-btn v-if="isActive(job)" size="small" variant="text" :icon="mdiClose" :aria-label="t('job.cancelNamed', { name: job.inputName })" :title="t('job.cancel')" :data-testid="`now-cancel-${job.id}`" @click="demo.cancel(job.id)" />
                <v-btn
                  v-else-if="job.state === 'failed' || job.state === 'cancelled'"
                  size="small"
                  variant="text"
                  color="primary"
                  :icon="mdiReplay"
                  :aria-label="t('job.retryNamed', { name: job.inputName })"
                  :title="t('job.retry')"
                  :data-testid="`now-retry-${job.id}`"
                  @click="demo.retry(job.id)"
                />
                <span v-else aria-hidden="true"></span>
                <JobProgress v-if="isActive(job)" :job="job" compact />
              </li>
            </ul>
          </AreaState>
        </div>
        <div class="u-row">
          <v-btn variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="open-queue-page" @click="layout.navigate('queue')">
            {{ hidden > 0 ? t("queue.openMore", { count: hidden }) : t("queue.open") }}
          </v-btn>
        </div>
        <div v-if="anyActive" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
          <v-chip label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
          <v-btn variant="outlined" :prepend-icon="mdiStepForward" data-testid="queue-step-all" @click="demo.stepAll()">{{ t("demo.stepAll") }}</v-btn>
        </div>
      </section>
    </div>
  </div>
</template>
