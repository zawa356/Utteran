<script setup lang="ts">
import { mdiArrowDown, mdiArrowUp, mdiClose, mdiReplay, mdiStepForward, mdiAlertOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";
import JobStateChip from "./JobStateChip.vue";

/**
 * Detail of the selected synthetic job: state, stage (never a percentage — G-03), the frozen
 * "settings at submit time", and the actions allowed in that state. Demo-only controls are
 * grouped separately and labelled as such.
 */
const { demo, state, t } = usePreview();
const job = computed(() => state.value.jobs.find((j) => j.id === state.value.selectedJobId) ?? null);
const snapshot = computed(() => {
  const s = job.value?.snapshot;
  if (!s) return "";
  return [
    t(`model.${s.model}`),
    t(`lang.${s.language}`),
    s.diarization ? t("summary.diarizationOn", { speakers: s.speakers === "auto" ? t("speakers.auto") : t("speakers.n", { n: s.speakers }) }) : t("summary.diarizationOff"),
    s.formats.map((f) => f.toUpperCase()).join(" · "),
  ].join(" / ");
});
</script>

<template>
  <v-card v-if="job" class="pa-3 u-stack" :aria-label="t('job.detail')" data-testid="job-detail">
    <div class="u-row">
      <h3 class="u-subheading p-ellipsis" :title="job.inputName">{{ job.inputName }}</h3>
      <JobStateChip :state="job.state" />
    </div>
    <dl class="p-dl text-body-2">
      <dt>{{ t("job.id") }}</dt>
      <dd>{{ job.id }} <span class="text-caption">({{ t("common.synthetic") }})</span></dd>
      <dt>{{ t("job.stageLabel") }}</dt>
      <dd>{{ job.stage ? t(`job.stage.${job.stage}`) : t("job.noStage") }}</dd>
      <dt>{{ t("job.attemptLabel") }}</dt>
      <dd>{{ job.attempt }}</dd>
      <dt>{{ t("job.snapshot") }}</dt>
      <dd data-testid="job-snapshot">{{ snapshot }}</dd>
    </dl>
    <p class="text-caption">{{ t("job.noPercent") }}</p>
    <v-alert v-if="job.state === 'failed'" type="error" role="alert" data-testid="job-error">
      {{ t("job.failedBody") }}
      <div class="text-caption">{{ t("area.errorCode", { code: job.errorCode ?? "—" }) }}</div>
    </v-alert>
    <div class="u-row">
      <v-btn v-if="job.state === 'waiting'" size="small" variant="tonal" :prepend-icon="mdiArrowUp" data-testid="job-up" @click="demo.move(job.id, -1)">{{ t("job.up") }}</v-btn>
      <v-btn v-if="job.state === 'waiting'" size="small" variant="tonal" :prepend-icon="mdiArrowDown" data-testid="job-down" @click="demo.move(job.id, 1)">{{ t("job.down") }}</v-btn>
      <v-btn v-if="isActive(job)" size="small" variant="tonal" :prepend-icon="mdiClose" data-testid="job-cancel" @click="demo.cancel(job.id)">{{ t("job.cancel") }}</v-btn>
      <v-btn v-if="job.state === 'failed' || job.state === 'cancelled'" size="small" color="primary" :prepend-icon="mdiReplay" data-testid="job-retry" @click="demo.retry(job.id)">{{ t("job.retry") }}</v-btn>
    </div>
    <div v-if="isActive(job)" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
      <v-chip size="x-small" label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
      <v-btn size="small" variant="outlined" :prepend-icon="mdiStepForward" data-testid="job-step" @click="demo.step(job.id)">{{ t("demo.step") }}</v-btn>
      <v-btn
        v-if="job.state !== 'waiting'"
        size="small"
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
