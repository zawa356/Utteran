<script setup lang="ts">
import { usePreview } from "../composables";
import type { AreaState } from "../domain/demo";

/**
 * Standard loading / error / empty presentation for one area. In the prototype these states are
 * switched explicitly (demo scenarios); "retry" only returns the area to ready — no request.
 */
defineProps<{ state: AreaState; empty: boolean; emptyText: string; testid: string }>();
const emit = defineEmits<{ retry: [] }>();
const { t } = usePreview();
</script>

<template>
  <div v-if="state === 'loading'" role="status" aria-live="polite" :data-testid="`${testid}-loading`">
    <v-skeleton-loader type="list-item-two-line@3" />
    <span class="text-body-medium">{{ t("area.loading") }}</span>
  </div>
  <v-alert v-else-if="state === 'error'" type="error" role="alert" :title="t('area.errorTitle')" :data-testid="`${testid}-error`">
    <div>{{ t("area.errorBody") }}</div>
    <div class="text-body-small">{{ t("area.errorCode", { code: "demo_unavailable" }) }}</div>
    <template #append>
      <v-btn variant="text" size="small" :data-testid="`${testid}-retry`" @click="emit('retry')">{{ t("common.retry") }}</v-btn>
    </template>
  </v-alert>
  <v-alert v-else-if="empty" type="info" variant="outlined" :data-testid="`${testid}-empty`">{{ emptyText }}</v-alert>
  <slot v-else />
</template>
