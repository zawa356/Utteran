<script setup lang="ts">
import AppIcon from "../components/AppIcon.vue";
import { usePreview } from "../composables";
import type { SetupMethod } from "../domain/setup";

/**
 * First page of the first run: what will happen, and how to set up (iteration 5, #30). The setup
 * method is NOT the app's Normal / Expert mode: anyone may choose "custom", and the environment page
 * can switch between the two without losing a choice.
 */
const { setup, setupState, t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-welcome">
    <div class="u-row"><AppIcon :size="40" /><h2 class="p-wizard__title">{{ t("wizard.welcome.title") }}</h2></div>
    <p class="text-body-large">{{ t("wizard.welcome.body") }}</p>
    <v-radio-group
      :model-value="setupState.choices.method"
      :label="t('wizard.method.label')"
      hide-details
      data-testid="wizard-method"
      @update:model-value="(v) => v && setup.setMethod(v as SetupMethod)"
    >
      <v-radio value="standard" color="primary" data-testid="wizard-method-standard">
        <template #label>
          <div>
            <div class="font-weight-medium">{{ t("wizard.method.standard") }}</div>
            <div class="text-body-medium">{{ t("wizard.method.standardHint") }}</div>
          </div>
        </template>
      </v-radio>
      <v-radio value="custom" color="primary" data-testid="wizard-method-custom">
        <template #label>
          <div>
            <div class="font-weight-medium">{{ t("wizard.method.custom") }}</div>
            <div class="text-body-medium">{{ t("wizard.method.customHint") }}</div>
          </div>
        </template>
      </v-radio>
    </v-radio-group>
  </div>
</template>
