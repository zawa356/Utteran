<script setup lang="ts">
import { usePreview } from "../composables";
import StepConfig from "./StepConfig.vue";
import StepInputs from "./StepInputs.vue";
import StepProgress from "./StepProgress.vue";
import StepResult from "./StepResult.vue";
import StepStart from "./StepStart.vue";

/** Normal mode A: one page, five numbered steps, top to bottom (no wizard, nothing hidden). */
const { t } = usePreview();
</script>

<template>
  <section class="u-page p-normal u-stack" aria-labelledby="normal-heading" data-testid="normal-flow">
    <h1 id="normal-heading" class="u-heading">{{ t("section.transcribe") }}</h1>
    <StepInputs />
    <StepConfig />
    <StepStart />
    <StepProgress />
    <StepResult />
  </section>
</template>
