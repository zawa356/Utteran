<script setup lang="ts">
import { usePreview } from "../composables";

/** Optional features. Only diarization today; it needs a model download and, the first time, a credential. */
const { setup, setupState, t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-features">
    <h2 class="p-wizard__title">{{ t("wizard.step.features") }}</h2>
    <v-switch
      :model-value="setupState.choices.diarization"
      :label="t('wizard.features.diarization')"
      color="primary"
      hide-details
      data-testid="wizard-diarization"
      @update:model-value="(v) => setup.setChoice('diarization', Boolean(v))"
    />
    <p class="text-body-medium">{{ t("wizard.features.diarizationHint") }}</p>
    <p v-if="setupState.choices.diarization && !setupState.detected.credential" class="text-body-medium" data-testid="wizard-needs-credential">{{ t("wizard.features.needsCredential") }}</p>
  </div>
</template>
