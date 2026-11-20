<script setup lang="ts">
import {
  mdiAlertCircleOutline, mdiCheckCircleOutline, mdiClockOutline, mdiInformationOutline,
  mdiPauseCircleOutline, mdiPlayCircleOutline, mdiStopCircleOutline,
} from "@mdi/js";
import { computed } from "vue";
import type { JobStatusV1 } from "../api/v1Resources";
import { useCodeText } from "../composables/v1";

const props = defineProps<{ value: JobStatusV1 }>();
const { status } = useCodeText();
const marker: Record<JobStatusV1, { color?: string; icon: string }> = {
  queued: { icon: mdiClockOutline },
  held: { icon: mdiPauseCircleOutline, color: "warning" },
  blocked: { icon: mdiAlertCircleOutline, color: "error" },
  running: { icon: mdiPlayCircleOutline, color: "primary" },
  waiting_user: { icon: mdiInformationOutline, color: "warning" },
  cancelling: { icon: mdiStopCircleOutline, color: "warning" },
  succeeded: { icon: mdiCheckCircleOutline, color: "success" },
  failed: { icon: mdiAlertCircleOutline, color: "error" },
  cancelled: { icon: mdiStopCircleOutline },
  interrupted: { icon: mdiPauseCircleOutline, color: "warning" },
};
const appearance = computed(() => marker[props.value] ?? { icon: mdiInformationOutline });
</script>

<template>
  <v-chip size="small" label variant="tonal" :prepend-icon="appearance.icon" :color="appearance.color" class="u-job-status">
    {{ status(value) }}
  </v-chip>
</template>

<style scoped>
.u-job-status {
  min-width: 0;
  max-width: 100%;
  height: auto;
  min-height: 24px;
  padding-block: 2px;
  white-space: normal;
}
.u-job-status :deep(.v-chip__content) {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
