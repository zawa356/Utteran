<script setup lang="ts">
import { mdiCloudOffOutline, mdiLanDisconnect, mdiSync, mdiCheckCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";

/**
 * Connection indicator. The honest default is "not connected" (the prototype has no backend);
 * the other variants are labelled as display examples so epoch:seq is never shown as real.
 */
const { state, t } = usePreview();
const view = computed(() => {
  switch (state.value.sync) {
    case "synced":
      return { icon: mdiCheckCircleOutline, color: "success", text: t("sync.synced") };
    case "resyncing":
      return { icon: mdiSync, color: "info", text: t("sync.resyncing") };
    case "offline":
      return { icon: mdiCloudOffOutline, color: "warning", text: t("sync.offline") };
    default:
      return { icon: mdiLanDisconnect, color: undefined, text: t("sync.none") };
  }
});
</script>

<template>
  <div class="u-row" role="status" data-testid="sync-status">
    <v-icon :icon="view.icon" :color="view.color" size="small" />
    <span class="text-body-2">{{ view.text }}</span>
    <v-chip v-if="state.sync !== 'none'" size="x-small" label variant="outlined">{{ t("common.example") }}</v-chip>
  </div>
</template>
