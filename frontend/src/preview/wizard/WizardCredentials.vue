<script setup lang="ts">
import { mdiCheckCircleOutline, mdiKeyOutline, mdiMinusCircleOutline } from "@mdi/js";
import { usePreview } from "../composables";

/**
 * Credential and model terms for diarization, shown only when they are needed. The prototype never
 * asks for a token value: "Register…" only flips the PRESENCE flag (the product opens a separate,
 * masked input that stores the token outside the settings and never shows it again).
 */
const { setup, setupState, t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-credentials">
    <h2 class="p-wizard__title">{{ t("wizard.step.credentials") }}</h2>
    <p class="text-body-medium">{{ t("wizard.cred.lead") }}</p>
    <div class="u-row" data-testid="wizard-credential-state">
      <v-icon :icon="setupState.choices.credential ? mdiCheckCircleOutline : mdiMinusCircleOutline" size="small" aria-hidden="true" />
      <span class="text-body-medium">{{ setupState.choices.credential ? t("wizard.cred.present") : t("wizard.cred.absent") }}</span>
      <v-btn v-if="!setupState.choices.credential" variant="tonal" color="primary" :prepend-icon="mdiKeyOutline" data-testid="wizard-register-credential" @click="setup.registerCredential()">{{ t("wizard.cred.register") }}</v-btn>
    </div>
    <v-checkbox
      :model-value="setupState.choices.agreement"
      :label="t('wizard.cred.agreement')"
      color="primary"
      hide-details
      data-testid="wizard-agreement"
      @update:model-value="(v) => setup.setChoice('agreement', Boolean(v))"
    />
    <div>
      <v-btn variant="text" data-testid="wizard-skip-diarization" @click="setup.skipDiarization()">{{ t("wizard.cred.skip") }}</v-btn>
    </div>
  </div>
</template>
