<script setup lang="ts">
import { mdiRefresh } from "@mdi/js";
import { usePreview } from "../composables";

/**
 * How fresh the job status is (iteration 3, owner memo #11). Nothing while it is current. A slow
 * refresh shows one quiet line only where jobs are shown and only while something is active; a
 * failed fetch shows a warning with retry. Neither claims that a job stopped: the job states stay
 * the last known ones, and the text says so. No connection jargon (sync, epoch, SSE) is shown.
 */
defineProps<{ active: boolean }>();
const { demo, state, t } = usePreview();
</script>

<template>
  <div v-if="state.sync === 'checking' && active" class="p-feedback" role="status" data-testid="status-checking">
    <v-progress-circular indeterminate size="16" width="2" aria-hidden="true" />
    <span>{{ t("status.checking") }}</span>
  </div>
  <v-alert v-else-if="state.sync === 'unavailable'" type="warning" variant="outlined" :title="t('status.unavailableTitle')" data-testid="status-unavailable">
    <div>{{ t("status.unavailableBody") }}</div>
    <v-btn class="mt-1 ml-n2" variant="text" color="primary" :prepend-icon="mdiRefresh" data-testid="status-retry" @click="demo.retrySync()">{{ t("common.retry") }}</v-btn>
  </v-alert>
</template>
