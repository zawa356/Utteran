<script setup lang="ts">
import { mdiCheckCircleOutline, mdiCloudOffOutline, mdiLanDisconnect, mdiSync } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";

/**
 * Connection indicator (Expert sidebar). The honest default is "not connected" (the prototype has
 * no backend); the other variants are labelled as display examples so epoch:seq is never shown as
 * real. `compact` = icon only (rail), with the text as tooltip/accessible name.
 */
withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });
const { state, t } = usePreview();
const view = computed(() => {
  switch (state.value.sync) {
    case "synced":
      return { icon: mdiCheckCircleOutline, color: "success", text: t("sync.synced"), short: t("sync.short.synced") };
    case "resyncing":
      return { icon: mdiSync, color: "info", text: t("sync.resyncing"), short: t("sync.short.resyncing") };
    case "offline":
      return { icon: mdiCloudOffOutline, color: "warning", text: t("sync.offline"), short: t("sync.short.offline") };
    default:
      return { icon: mdiLanDisconnect, color: undefined, text: t("sync.none"), short: t("sync.short.none") };
  }
});
</script>

<template>
  <div class="p-sync" role="status" :title="view.text" data-testid="sync-status">
    <v-icon :icon="view.icon" :color="view.color" size="small" aria-hidden="true" />
    <span v-if="!compact" class="text-body-small p-ellipsis" aria-hidden="true">{{ view.short }}</span>
    <span class="p-sr-only">{{ view.text }}</span>
    <v-chip v-if="state.sync !== 'none' && !compact" size="x-small" label variant="outlined">{{ t("common.example") }}</v-chip>
  </div>
</template>
