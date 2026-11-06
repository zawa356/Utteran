<script setup lang="ts">
import { mdiCheckCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { planFor } from "../domain/setup";
import { useSetupText } from "./useSetupText";

/**
 * Completion (iteration 5, #28): a title and short label/value lines — no feature explanations.
 * One small line says that the prototype installed nothing (the start button is right below, so the
 * fact must not be missed); the shared banner says the same for the whole app.
 */
const { setupState, t } = usePreview();
const { modelName } = useSetupText();
const c = computed(() => setupState.value.choices);
const added = computed(() => planFor(setupState.value.entry, c.value, setupState.value.inventory).newModels);
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-done">
    <div class="u-row">
      <v-icon :icon="mdiCheckCircleOutline" color="success" aria-hidden="true" />
      <h2 class="p-wizard__title">{{ t(setupState.entry === "repair" ? "wizard.done.repairTitle" : "wizard.done.title") }}</h2>
    </div>
    <dl class="p-dl text-body-medium" data-testid="wizard-done-summary">
      <dt>{{ t("wizard.done.device") }}</dt>
      <dd>{{ c.device === "gpu" ? "GPU" : "CPU" }}</dd>
      <template v-if="setupState.entry !== 'repair'">
        <dt>{{ t("wizard.done.models") }}</dt>
        <dd data-testid="wizard-done-models">{{ (added.length > 0 ? added : c.models).map(modelName).join(t("list.separator")) }}</dd>
        <dt>{{ t("field.diarization") }}</dt>
        <dd data-testid="wizard-done-diarization">{{ c.diarization ? t("common.enabled") : t("common.disabled") }}</dd>
      </template>
    </dl>
    <p class="text-body-small" data-testid="wizard-done-note">{{ t("wizard.done.note") }}</p>
  </div>
</template>
