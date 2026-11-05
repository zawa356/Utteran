<script setup lang="ts">
import { mdiCheck, mdiCircleOutline, mdiMinus, mdiPlay } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import type { DemoJob, JobStage } from "../domain/demo";

/**
 * Stage track ASR → diarization → output for one job: done (✓), current (▶), pending (○),
 * skipped (–, diarization off in the snapshot). Text + icon per step; no percentage (G-03).
 */
const props = defineProps<{ job: DemoJob }>();
const { t } = usePreview();
const STAGES: JobStage[] = ["asr", "diarization", "output"];
type StepState = "done" | "current" | "pending" | "skipped";
const steps = computed(() => {
  const j = props.job;
  const at = j.stage ? STAGES.indexOf(j.stage) : -1;
  return STAGES.map((stage, i): { stage: JobStage; status: StepState } => {
    if (stage === "diarization" && !j.snapshot.diarization) return { stage, status: "skipped" };
    if (j.state === "succeeded") return { stage, status: "done" };
    if (j.state !== "running" || at < 0) return { stage, status: "pending" };
    if (i < at) return { stage, status: "done" };
    return { stage, status: i === at ? "current" : "pending" };
  });
});
const ICON: Record<StepState, string> = { done: mdiCheck, current: mdiPlay, pending: mdiCircleOutline, skipped: mdiMinus };
</script>

<template>
  <ol class="p-track" :aria-label="t('progress.stages')" data-testid="stage-track">
    <li v-for="s in steps" :key="s.stage" :class="`p-track__step p-track__step--${s.status}`" :data-testid="`stage-${s.stage}`" :data-status="s.status">
      <v-icon :icon="ICON[s.status]" size="x-small" aria-hidden="true" />
      <span class="text-body-medium">{{ t(`job.stage.${s.stage}`) }}</span>
      <span class="p-sr-only">（{{ t(`stage.status.${s.status}`) }}）</span>
      <span v-if="s.status === 'current' || s.status === 'skipped'" class="text-body-small" aria-hidden="true">{{ t(`stage.status.${s.status}`) }}</span>
    </li>
  </ol>
</template>
