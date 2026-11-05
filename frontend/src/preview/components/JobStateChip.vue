<script setup lang="ts">
import { mdiAlertCircleOutline, mdiCancel, mdiCheckCircleOutline, mdiClockOutline, mdiPlayCircleOutline, mdiProgressWrench } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import type { JobState } from "../domain/demo";

/** State = icon + label (+ colour as a third cue). Colour alone never carries the meaning. */
const props = withDefaults(defineProps<{ state: JobState; size?: "x-small" | "small" }>(), { size: "small" });
const { t } = usePreview();
const VIEW: Record<JobState, { color: string | undefined; icon: string }> = {
  waiting: { color: undefined, icon: mdiClockOutline },
  preparing: { color: "info", icon: mdiProgressWrench },
  running: { color: "primary", icon: mdiPlayCircleOutline },
  succeeded: { color: "success", icon: mdiCheckCircleOutline },
  failed: { color: "error", icon: mdiAlertCircleOutline },
  cancelled: { color: "secondary", icon: mdiCancel },
};
const view = computed(() => VIEW[props.state]);
</script>

<template>
  <v-chip :size="size" label :color="view.color" variant="tonal" :prepend-icon="view.icon" :data-testid="`job-state-${state}`">{{ t(`job.state.${state}`) }}</v-chip>
</template>
