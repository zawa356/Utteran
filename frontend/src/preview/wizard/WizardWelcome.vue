<script setup lang="ts">
import AppIcon from "../components/AppIcon.vue";
import { usePreview } from "../composables";

const { t } = usePreview();
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-welcome">
    <div class="u-row"><AppIcon :size="40" /><h2 class="p-wizard__title">{{ t("wizard.welcome.title") }}</h2></div>
    <p class="text-body-large">{{ t("wizard.welcome.body") }}</p>
    <ol class="p-wizard__outline text-body-medium">
      <li>{{ t("wizard.step.environment") }}</li>
      <li>{{ t("wizard.step.features") }}</li>
      <li>{{ t("wizard.step.review") }}</li>
    </ol>
  </div>
</template>
