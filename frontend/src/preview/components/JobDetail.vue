<script setup lang="ts">
import { mdiAlertOutline, mdiClose, mdiPercentOutline, mdiReplay, mdiStepForward } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";
import JobProgress from "./JobProgress.vue";
import JobStageTrack from "./JobStageTrack.vue";
import { useSnapshotText } from "./useSnapshotText";
import JobStateChip from "./JobStateChip.vue";

/**
 * Detail of the selected synthetic job: state, stage and the fictional stage progress/ETA, the frozen
 * "settings at submit time", and the actions allowed in that state. Demo-only controls are
 * grouped separately and labelled as such.
 */
const { demo, state, t } = usePreview();
const job = computed(() => state.value.jobs.find((j) => j.id === state.value.selectedJobId) ?? null);
const { snapshotText } = useSnapshotText();
const snapshot = computed(() => (job.value ? snapshotText(job.value.snapshot) : ""));
</script>

<template>
  <v-card v-if="job" class="pa-3 u-stack" :aria-label="t('job.detail')" data-testid="job-detail">
    <div class="u-row">
      <h3 class="u-subheading p-ellipsis" :title="job.inputName">{{ job.inputName }}</h3>
      <JobStateChip :state="job.state" />
    </div>
    <dl class="p-dl text-body-medium">
      <dt>{{ t("job.id") }}</dt>
      <dd>{{ job.id }}</dd>
      <dt>{{ t("job.attemptLabel") }}</dt>
      <dd>{{ job.attempt }}</dd>
      <dt>{{ t("job.snapshot") }}</dt>
      <dd data-testid="job-snapshot">{{ snapshot }}</dd>
    </dl>
    <JobStageTrack :job="job" />
    <JobProgress v-if="isActive(job)" :job="job" />
    <v-alert v-if="job.state === 'failed'" type="error" role="alert" data-testid="job-error">
      {{ t("job.failedBody") }}
      <div class="text-body-small">{{ t("area.errorCode", { code: job.errorCode ?? "—" }) }}</div>
    </v-alert>
    <div class="u-row">
      <v-btn v-if="isActive(job)" variant="outlined" :prepend-icon="mdiClose" data-testid="job-cancel" @click="demo.cancel(job.id)">{{ t("job.cancel") }}</v-btn>
      <v-btn v-if="job.state === 'failed' || job.state === 'cancelled'" color="primary" :prepend-icon="mdiReplay" data-testid="job-retry" @click="demo.retry(job.id)">{{ t("job.retry") }}</v-btn>
    </div>
    <div v-if="isActive(job)" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
      <v-chip label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
      <v-btn v-if="job.state === 'running' && job.progress.fraction !== null && job.progress.fraction < 1" variant="outlined" :prepend-icon="mdiPercentOutline" data-testid="job-advance" @click="demo.advance(job.id)">{{ t("demo.advance") }}</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiStepForward" data-testid="job-step" @click="demo.step(job.id)">{{ t("demo.step") }}</v-btn>
      <v-btn
        v-if="job.state !== 'waiting'"
        variant="outlined"
        :prepend-icon="mdiAlertOutline"
        data-testid="job-fail"
        @click="demo.fail(job.id)"
      >
        {{ t("demo.fail") }}
      </v-btn>
    </div>
  </v-card>
  <v-alert v-else type="info" variant="outlined" data-testid="job-detail-empty">{{ t("job.selectHint") }}</v-alert>
</template>
