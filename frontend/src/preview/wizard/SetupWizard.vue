<script setup lang="ts">
import { mdiArrowLeft, mdiArrowRight, mdiPlay } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { nextBlock, wizardSteps } from "../domain/setup";
import type { PreviewKey } from "../i18n";
import WizardCredentials from "./WizardCredentials.vue";
import WizardDone from "./WizardDone.vue";
import WizardEnvironment from "./WizardEnvironment.vue";
import WizardFeatures from "./WizardFeatures.vue";
import WizardReview from "./WizardReview.vue";
import WizardRun from "./WizardRun.vue";
import WizardWelcome from "./WizardWelcome.vue";

/**
 * Setup wizard (mock, iteration 4): one page per step, the same button positions on every page —
 * close on the left; back and next/start on the right. The step position is a quiet line, not a
 * stepper. First run, "add" and "repair" reuse these pages but start at different steps with
 * different plans. Closing keeps every choice (in memory only; a reload starts over).
 */
const { layout, setup, setupState, t } = usePreview();
const step = computed(() => setupState.value.step);
const steps = computed(() => wizardSteps(setupState.value));
const index = computed(() => steps.value.indexOf(step.value));
const block = computed(() => nextBlock(setupState.value));
const confirming = ref(false);
const title = computed(() => t(`wizard.title.${setupState.value.entry}` as PreviewKey));
const position = computed(() => {
  if (step.value === "run") return t("wizard.position.run");
  if (step.value === "done") return t("wizard.position.done");
  return t("wizard.position", { i: index.value + 1, n: steps.value.length, name: t(`wizard.step.${step.value}` as PreviewKey) });
});
const canClose = computed(() => setupState.value.run !== "running");
function requestClose() {
  if (step.value === "done" || step.value === "run" || (step.value === steps.value[0] && setupState.value.entry !== "first_run")) setup.close();
  else confirming.value = true;
}
function confirmClose() {
  confirming.value = false;
  setup.close();
}
function startTranscribing() {
  const firstRun = setupState.value.entry === "first_run";
  setup.finish();
  if (firstRun) layout.setMode("normal");
  layout.navigate("transcribe");
}
function backToSetup() {
  setup.finish();
  layout.navigate("setup");
}
</script>

<template>
  <section class="p-wizard" aria-labelledby="wizard-heading" data-testid="setup-wizard" :data-step="step" :data-entry="setupState.entry">
    <header class="p-wizard__head">
      <h1 id="wizard-heading" class="u-heading">{{ title }}</h1>
      <span class="text-body-medium" data-testid="wizard-position">{{ position }}</span>
    </header>
    <div class="p-wizard__body">
      <WizardWelcome v-if="step === 'welcome'" />
      <WizardEnvironment v-else-if="step === 'environment'" />
      <WizardFeatures v-else-if="step === 'features'" />
      <WizardCredentials v-else-if="step === 'credentials'" />
      <WizardReview v-else-if="step === 'review'" />
      <WizardRun v-else-if="step === 'run'" />
      <WizardDone v-else />
      <p v-if="block" class="p-feedback mt-3" role="status" data-testid="wizard-next-block">{{ t(`wizard.block.${block}` as PreviewKey) }}</p>
    </div>
    <footer class="p-wizard__foot">
      <v-btn v-if="step !== 'done'" variant="text" :disabled="!canClose" data-testid="wizard-close" @click="requestClose">{{ step === "run" ? t("common.close") : t("wizard.later") }}</v-btn>
      <v-spacer />
      <template v-if="step === 'done'">
        <v-btn v-if="setupState.entry !== 'first_run'" variant="text" data-testid="wizard-back-to-setup" @click="backToSetup">{{ t("wizard.done.backToSetup") }}</v-btn>
        <v-btn color="primary" :prepend-icon="mdiPlay" data-testid="wizard-start-transcribing" @click="startTranscribing">{{ t("wizard.done.start") }}</v-btn>
      </template>
      <template v-else-if="step !== 'run'">
        <v-btn variant="outlined" :prepend-icon="mdiArrowLeft" :disabled="index <= 0" data-testid="wizard-back" @click="setup.back()">{{ t("wizard.back") }}</v-btn>
        <v-btn v-if="step === 'review'" color="primary" :prepend-icon="mdiPlay" data-testid="wizard-approve" @click="setup.approveAndStart()">{{ t("wizard.approve") }}</v-btn>
        <v-btn v-else color="primary" :append-icon="mdiArrowRight" :disabled="block !== null" data-testid="wizard-next" @click="setup.next()">{{ t("wizard.next") }}</v-btn>
      </template>
    </footer>

    <v-dialog v-model="confirming" max-width="440" data-testid="wizard-close-dialog">
      <v-card variant="elevated" :title="t('wizard.closeTitle')">
        <v-card-text>{{ t("wizard.closeBody") }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-testid="wizard-close-cancel" @click="confirming = false">{{ t("wizard.closeStay") }}</v-btn>
          <v-btn color="primary" variant="tonal" data-testid="wizard-close-confirm" @click="confirmClose">{{ t("wizard.closeConfirm") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
