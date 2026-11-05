<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { currentStage } from "../domain/setup";
import type { PreviewKey } from "../i18n";
import { useSetupNav } from "./useSetupNav";

/**
 * One line while setup is not finished (the wizard was closed, or an operation stopped): what state
 * it is in and the button to continue. Nothing when a configuration is usable.
 */
const { setupState, t } = usePreview();
const { openSetup } = useSetupNav();
const text = computed(() => {
  const s = setupState.value;
  if (s.configured) return null;
  const stage = currentStage(s);
  if (s.run === "interrupted" && stage) return t("setupNotice.interrupted", { stage: t(`stage.${stage.id}` as PreviewKey) });
  if (s.run === "failed") return t("setupNotice.failed");
  return t("setupNotice.notDone");
});
</script>

<template>
  <div v-if="text" class="p-notice" role="status" data-testid="setup-notice">
    <span class="text-body-medium">{{ text }}</span>
    <v-btn color="primary" variant="tonal" data-testid="setup-notice-continue" @click="openSetup('first_run')">{{ t("setupNotice.continue") }}</v-btn>
  </div>
</template>
