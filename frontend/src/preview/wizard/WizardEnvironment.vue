<script setup lang="ts">
import { mdiCheckCircleOutline, mdiMinusCircleOutline } from "@mdi/js";
import { usePreview } from "../composables";
import type { DevicePlan } from "../domain/setup";

/**
 * Detected (fictional) facts and the one choice that matters: GPU or CPU. A GPU that was not found
 * cannot be chosen; the CPU always can — the wizard never forces a GPU.
 */
const { setup, setupState, t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-environment">
    <h2 class="p-wizard__title">{{ t("wizard.step.environment") }}</h2>
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
            <div class="font-weight-medium">{{ t("wizard.env.gpuOption") }}<span v-if="setupState.detected.gpu"> — {{ t("env.recommended") }}</span></div>
            <div class="text-body-medium">{{ setupState.detected.gpu ? t("wizard.env.gpuHint") : t("wizard.env.gpuUnavailable") }}</div>
          </div>
        </template>
      </v-radio>
      <v-radio value="cpu" color="primary" data-testid="wizard-device-cpu">
        <template #label>
          <div>
            <div class="font-weight-medium">{{ t("wizard.env.cpuOption") }}<span v-if="!setupState.detected.gpu"> — {{ t("env.recommended") }}</span></div>
            <div class="text-body-medium">{{ t("wizard.env.cpuHint") }}</div>
          </div>
        </template>
      </v-radio>
    </v-radio-group>
  </div>
</template>
