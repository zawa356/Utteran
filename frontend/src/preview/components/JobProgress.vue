<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { type DemoJob, stageEta, stagePosition } from "../domain/demo";
import { formatDuration } from "../domain/fixtures";

/**
 * Progress / ETA mock (iteration 3, owner memo #6; G-03 preview). FICTIONAL numbers that only move
 * on demo controls. What it shows, and what it deliberately does not:
 *  - the current stage ("2/3 話者分離") and the share of THAT stage done (a reported value);
 *  - elapsed time and the stage's remaining time, marked "estimated"; "estimating" while too early;
 *  - an indeterminate bar when the stage reports no progress (output) or the job is preparing;
 *  - never a job-wide percentage made up from fixed stage weights.
 * `compact` = one line for the Expert short queue.
 */
const props = withDefaults(defineProps<{ job: DemoJob; compact?: boolean }>(), { compact: false });
const { t } = usePreview();

const position = computed(() => stagePosition(props.job));
const fraction = computed(() => (props.job.state === "running" ? props.job.progress.fraction : null));
const percent = computed(() => (fraction.value === null ? null : Math.round(fraction.value * 100)));
const eta = computed(() => stageEta(props.job));
function approx(sec: number): string {
  if (sec < 60) return t("eta.underMinute");
  return t("eta.minutes", { n: Math.round(sec / 60) });
}
const etaText = computed(() => {
  const e = eta.value;
  if (e.kind === "estimating") return t("eta.estimating");
  if (e.kind === "estimate") return t("eta.remaining", { time: approx(e.remainingSec) });
  return null;
});
const stageText = computed(() => {
  const j = props.job;
  if (j.state === "waiting") return t("progress.waiting");
  if (j.state === "preparing") return t("progress.preparing");
  if (!j.stage || !position.value) return "";
  return t("progress.stage", { index: position.value.index, total: position.value.total, stage: t(`job.stage.${j.stage}`) });
});
const label = computed(() => [stageText.value, percent.value === null ? null : t("progress.percent", { n: percent.value }), etaText.value].filter(Boolean).join(" · "));
/** One short line for the Expert short queue: "話者分離 50% · 残り 約2分（推定）". */
const compactText = computed(() => {
  const j = props.job;
  if (j.state !== "running" || !j.stage) return stageText.value;
  const stage = percent.value === null ? t(`job.stage.${j.stage}`) : t("progress.compact", { stage: t(`job.stage.${j.stage}`), n: percent.value });
  const e = eta.value;
  const etaShort = e.kind === "estimate" ? t("eta.short", { time: approx(e.remainingSec) }) : e.kind === "estimating" ? t("eta.estimating") : null;
  return [stage, etaShort].filter(Boolean).join(" · ");
});
</script>

<template>
  <div v-if="compact" class="p-mini__sub" :data-testid="`progress-${job.id}`">
    <span :title="label">{{ compactText }}</span>
    <v-progress-linear
      v-if="job.state === 'running' || job.state === 'preparing'"
      class="mt-1"
      :model-value="percent ?? 0"
      :indeterminate="percent === null"
      color="primary"
      height="4"
      rounded
      :aria-label="label"
    />
  </div>
  <div v-else class="p-progress" :data-testid="`progress-${job.id}`">
    <div class="p-progress__line">
      <span class="font-weight-medium" data-testid="progress-stage">{{ stageText }}</span>
      <span v-if="percent !== null" class="font-weight-medium" data-testid="progress-percent">{{ t("progress.percent", { n: percent }) }}</span>
    </div>
    <v-progress-linear
      v-if="job.state === 'running' || job.state === 'preparing'"
      :model-value="percent ?? 0"
      :indeterminate="percent === null"
      color="primary"
      height="8"
      rounded
      :aria-label="label"
      data-testid="progress-bar"
      :data-indeterminate="percent === null"
    />
    <div v-if="job.state === 'running'" class="p-progress__nums">
      <span data-testid="progress-elapsed">{{ t("progress.elapsed", { time: formatDuration(job.progress.elapsedSec) }) }}</span>
      <span v-if="etaText" data-testid="progress-eta">{{ etaText }}</span>
      <span v-else data-testid="progress-no-eta">{{ t("eta.none") }}</span>
    </div>
  </div>
</template>
