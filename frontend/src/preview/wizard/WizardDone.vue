<script setup lang="ts">
import { mdiCheckCircleOutline } from "@mdi/js";
import { usePreview } from "../composables";

const { setupState, t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-done">
    <div class="u-row">
      <v-icon :icon="mdiCheckCircleOutline" color="success" aria-hidden="true" />
      <h2 class="p-wizard__title">{{ t(setupState.entry === "repair" ? "wizard.done.repairTitle" : "wizard.done.title") }}</h2>
    </div>
    <dl v-if="setupState.entry !== 'repair'" class="p-dl text-body-medium" data-testid="wizard-done-summary">
      <dt>{{ t("wizard.env.choose") }}</dt>
      <dd>{{ setupState.choices.device === "gpu" ? t("wizard.env.gpuOption") : t("wizard.env.cpuOption") }}</dd>
      <dt>{{ t("wizard.features.diarization") }}</dt>
      <dd>{{ setupState.choices.diarization ? t("common.on") : t("common.off") }}</dd>
    </dl>
    <p class="text-body-medium" data-testid="wizard-done-note">{{ t("wizard.done.note") }}</p>
  </div>
</template>
