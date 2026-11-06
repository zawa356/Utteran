<script setup lang="ts">
import { mdiCheckCircleOutline, mdiMinusCircleOutline } from "@mdi/js";
import { usePreview } from "../composables";
import type { DevicePlan, SetupMethod } from "../domain/setup";
import WizardModels from "./WizardModels.vue";

/**
 * Detected (fictional) facts, the device and the models (iteration 5, #30). A GPU that was not
 * found cannot be chosen; the CPU always can — the wizard never forces a GPU. Standard and custom
 * edit the same choices, so the switch at the top never loses a selection.
 */
const { setup, setupState, state, t } = usePreview();
// Adding to a working setup: say which profile is already set up (choosing it again adds models only).
const installed = (p: DevicePlan) => setupState.value.entry !== "first_run" && state.value.env[p] === "ready";
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-environment">
    <div class="u-row">
      <h2 class="p-wizard__title">{{ t("wizard.step.environment") }}</h2>
      <v-spacer />
      <v-btn-toggle
        :model-value="setupState.choices.method"
        mandatory
        divided
        density="compact"
        variant="outlined"
        color="primary"
        :aria-label="t('wizard.method.label')"
        data-testid="wizard-method-toggle"
        @update:model-value="(v: SetupMethod) => setup.setMethod(v)"
      >
        <v-btn value="standard" data-testid="wizard-method-toggle-standard">{{ t("wizard.method.standard") }}</v-btn>
        <v-btn value="custom" data-testid="wizard-method-toggle-custom">{{ t("wizard.method.custom") }}</v-btn>
      </v-btn-toggle>
    </div>
    <dl class="p-dl text-body-medium" data-testid="wizard-detected">
      <dt>{{ t("wizard.env.gpu") }}</dt>
      <dd>
        <v-icon :icon="setupState.detected.gpu ? mdiCheckCircleOutline : mdiMinusCircleOutline" size="small" aria-hidden="true" />
        {{ setupState.detected.gpu ? t("wizard.env.gpuFound") : t("wizard.env.gpuNone") }}
      </dd>
      <dt>{{ t("wizard.env.disk") }}</dt>
      <dd>{{ t("wizard.env.diskValue") }}</dd>
    </dl>
    <v-radio-group
      :model-value="setupState.choices.device"
      :label="t('wizard.env.choose')"
      hide-details
      data-testid="wizard-device"
      @update:model-value="(v) => v && setup.setChoice('device', v as DevicePlan)"
    >
      <v-radio value="gpu" :disabled="!setupState.detected.gpu" color="primary" data-testid="wizard-device-gpu">
        <template #label>
          <div>
            <div class="font-weight-medium">{{ t("wizard.env.gpuOption") }}<span v-if="installed('gpu')" data-testid="wizard-installed-gpu"> — {{ t("wizard.env.installed") }}</span><span v-else-if="setupState.detected.gpu"> — {{ t("env.recommended") }}</span></div>
            <div class="text-body-medium">{{ setupState.detected.gpu ? t("wizard.env.gpuHint") : t("wizard.env.gpuUnavailable") }}</div>
          </div>
        </template>
      </v-radio>
      <v-radio value="cpu" color="primary" data-testid="wizard-device-cpu">
        <template #label>
          <div>
            <div class="font-weight-medium">{{ t("wizard.env.cpuOption") }}<span v-if="installed('cpu')" data-testid="wizard-installed-cpu"> — {{ t("wizard.env.installed") }}</span><span v-else-if="!setupState.detected.gpu"> — {{ t("env.recommended") }}</span></div>
            <div class="text-body-medium">{{ t("wizard.env.cpuHint") }}</div>
          </div>
        </template>
      </v-radio>
    </v-radio-group>
    <WizardModels />
  </div>
</template>
