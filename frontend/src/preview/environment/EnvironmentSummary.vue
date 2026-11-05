<script setup lang="ts">
import { mdiArrowRight, mdiChip } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import DemoInfoChip from "./DemoInfoChip.vue";
import EnvStatusChip from "./EnvStatusChip.vue";
import { useEnvironment } from "./useEnvironment";

/**
 * One-line summary of the fictional environment (Normal home, Expert Inspector). It names the
 * recommended example profile and links to Setup for the details. Always labelled as demo data.
 */
withDefaults(defineProps<{ link?: boolean }>(), { link: true });
const { layout, t } = usePreview();
const { view, primary, fact, checking } = useEnvironment();
const line = computed(() => {
  if (!primary.value) return "";
  return [fact("asr"), fact("model"), fact("device") ?? fact("runtime")].filter((x) => x).join(" · ");
});
</script>

<template>
  <div class="p-envline" data-testid="env-summary">
    <v-icon :icon="mdiChip" size="small" aria-hidden="true" />
    <span class="text-body-medium font-weight-medium">{{ t("env.summaryLabel") }}</span>
    <template v-if="checking">
      <span class="text-body-medium" data-testid="env-summary-checking">{{ t("env.checking") }}</span>
    </template>
    <template v-else-if="view.loadFailed">
      <EnvStatusChip status="unavailable" />
      <span class="text-body-medium" data-testid="env-summary-text">{{ t("env.loadFailedShort") }}</span>
    </template>
    <template v-else-if="primary">
      <span class="text-body-medium" data-testid="env-summary-text">{{ t(`env.profile.${primary.name}`) }}<template v-if="line"> — {{ line }}</template></span>
      <EnvStatusChip :status="primary.status" />
    </template>
    <DemoInfoChip />
    <v-btn v-if="link" size="x-small" variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="env-summary-link" @click="layout.navigate('setup')">{{ t("env.details") }}</v-btn>
  </div>
</template>
