<script setup lang="ts">
import { mdiCheckCircleOutline, mdiKeyOutline, mdiMinusCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { credentialPresent, DIARIZATION_TERMS, planFor } from "../domain/setup";

/**
 * Credential and model terms for diarization — two separate states (iteration 4 P1 fix): a
 * credential that already exists does NOT accept this plan's terms. "Register…" appears only while
 * no credential exists; the terms checkbox appears whenever the plan requires them. The prototype
 * never asks for a token value: "Register…" only flips the PRESENCE flag (the product opens a
 * separate, masked input that stores the token outside the settings and never shows it again).
 */
const { setup, setupState, t } = usePreview();
const plan = computed(() => planFor(setupState.value.entry, setupState.value.choices));
const hasCredential = computed(() => credentialPresent(setupState.value));
const accepted = computed(() => setupState.value.choices.acceptedAgreements.includes(DIARIZATION_TERMS));
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-credentials">
    <h2 class="p-wizard__title">{{ t("wizard.step.credentials") }}</h2>
    <p class="text-body-medium">{{ t("wizard.cred.lead") }}</p>
    <div v-if="plan.requiresCredential" class="u-row" data-testid="wizard-credential-state" :data-present="hasCredential">
      <v-icon :icon="hasCredential ? mdiCheckCircleOutline : mdiMinusCircleOutline" size="small" aria-hidden="true" />
      <span class="text-body-medium">{{ hasCredential ? t("wizard.cred.present") : t("wizard.cred.absent") }}</span>
      <v-btn v-if="!hasCredential" variant="tonal" color="primary" :prepend-icon="mdiKeyOutline" data-testid="wizard-register-credential" @click="setup.registerCredential()">{{ t("wizard.cred.register") }}</v-btn>
    </div>
    <v-checkbox
      v-if="plan.requiredAgreements.includes(DIARIZATION_TERMS)"
      :model-value="accepted"
      :label="t('wizard.cred.agreement')"
      color="primary"
      hide-details
      data-testid="wizard-agreement"
      @update:model-value="(v) => setup.setAgreement(DIARIZATION_TERMS, Boolean(v))"
    />
    <div>
      <v-btn variant="text" data-testid="wizard-skip-diarization" @click="setup.skipDiarization()">{{ t("wizard.cred.skip") }}</v-btn>
    </div>
  </div>
</template>
