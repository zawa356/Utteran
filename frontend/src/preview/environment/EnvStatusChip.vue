<script setup lang="ts">
import { mdiAlertOutline, mdiCheckCircleOutline, mdiCloseCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import type { ProfileStatus } from "../domain/envExamples";

/** Status of a FICTIONAL profile: icon + label (the Setup page states once that all of it is fictional). */
const props = defineProps<{ status: ProfileStatus }>();
const { t } = usePreview();
const VIEW: Record<ProfileStatus, { color: string; icon: string }> = {
  ready: { color: "success", icon: mdiCheckCircleOutline },
  warning: { color: "warning", icon: mdiAlertOutline },
  unavailable: { color: "error", icon: mdiCloseCircleOutline },
};
const view = computed(() => VIEW[props.status]);
</script>

<template>
  <v-chip label variant="tonal" :color="view.color" :prepend-icon="view.icon" :data-testid="`env-status-${status}`">{{ t(`env.status.${status}`) }}</v-chip>
</template>
