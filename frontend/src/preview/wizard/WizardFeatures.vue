<script setup lang="ts">
import { mdiCheckCircleOutline, mdiKeyOutline, mdiMinusCircleOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { credentialPresent, DIARIZATION_TERMS } from "../domain/setup";

/**
 * Optional features (iteration 5, #26/#28). Diarization is explained once, here. Switching it on
 * shows — on this same page — the token state ("Register…" only while none exists) and the model
 * terms. They are two separate requirements (iteration 4 P1-1): a token that already exists does
 * NOT accept this plan's terms. The prototype never asks for a token value: "Register…" only
 * flips the PRESENCE flag (the product opens a separate, masked input that stores the token
 * outside the settings and never shows it again). Off: neither row is shown or required.
 */
const { setup, setupState, state, t } = usePreview();
const on = computed(() => setupState.value.choices.diarization);
const hasCredential = computed(() => credentialPresent(setupState.value));
const accepted = computed(() => setupState.value.choices.acceptedAgreements.includes(DIARIZATION_TERMS));
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-features">
    <h2 class="p-wizard__title">{{ t("wizard.step.features") }}</h2>
    <div>
      <v-switch
        :model-value="on"
        :label="t('wizard.features.diarization')"
        color="primary"
        hide-details
        data-testid="wizard-diarization"
        @update:model-value="(v) => setup.setChoice('diarization', Boolean(v))"
      />
      <p class="text-body-medium">{{ t("wizard.features.diarizationHint") }}</p>
    </div>
    <p v-if="setupState.entry !== 'first_run' && state.env.diarization && !on" class="text-body-medium" data-testid="wizard-diarization-kept">{{ t("wizard.features.alreadySetUp") }}</p>

    <section v-if="on" class="p-inline-req u-stack" aria-labelledby="wizard-cred-heading" data-testid="wizard-credentials">
      <h3 id="wizard-cred-heading" class="text-body-large font-weight-medium">{{ t("wizard.cred.title") }}</h3>
      <div class="u-row" data-testid="wizard-credential-state" :data-present="hasCredential">
        <v-icon :icon="hasCredential ? mdiCheckCircleOutline : mdiMinusCircleOutline" :color="hasCredential ? 'success' : undefined" size="small" aria-hidden="true" />
        <span class="text-body-medium">{{ hasCredential ? t("wizard.cred.present") : t("wizard.cred.absent") }}</span>
        <v-btn v-if="!hasCredential" variant="tonal" color="primary" :prepend-icon="mdiKeyOutline" data-testid="wizard-register-credential" @click="setup.registerCredential()">{{ t("wizard.cred.register") }}</v-btn>
      </div>
      <v-checkbox
        :model-value="accepted"
        :label="t('wizard.cred.agreement')"
        color="primary"
        hide-details
        data-testid="wizard-agreement"
        @update:model-value="(v) => setup.setAgreement(DIARIZATION_TERMS, Boolean(v))"
      />
    </section>
  </div>
</template>
