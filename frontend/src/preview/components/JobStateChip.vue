<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import type { JobState } from "../domain/demo";

const props = defineProps<{ state: JobState }>();
const { t } = usePreview();
const COLOR: Record<JobState, string | undefined> = {
  waiting: undefined,
  preparing: "info",
  running: "primary",
  succeeded: "success",
  failed: "error",
  cancelled: "secondary",
};
const color = computed(() => COLOR[props.state]);
</script>

<template>
  <v-chip size="small" label :color="color" variant="tonal" :data-testid="`job-state-${state}`">{{ t(`job.state.${state}`) }}</v-chip>
</template>
