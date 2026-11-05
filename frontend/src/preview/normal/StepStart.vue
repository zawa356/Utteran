<script setup lang="ts">
import { mdiPlay } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { submittedInputs } from "../domain/demo";
import StepCard from "./StepCard.vue";

/** One primary action. When it cannot run, the reason is shown where the button is. */
const { demo, state, t } = usePreview();
const startable = computed(() => {
  const submitted = submittedInputs(state.value);
  return state.value.inputs.filter((i) => i.supported && !submitted.has(i.id));
});
const blocked = computed(() => {
  if (state.value.inputsArea !== "ready") return t("start.blocked.area");
  if (state.value.inputs.filter((i) => i.supported).length === 0) return t("start.blocked.noInputs");
  if (startable.value.length === 0) return t("start.blocked.allSubmitted");
  return null;
});
</script>

<template>
  <StepCard :n="3" :title="t('step.start')" testid="step-start">
    <div class="u-row">
      <v-btn color="primary" :prepend-icon="mdiPlay" :disabled="blocked !== null" data-testid="normal-start" @click="demo.submit(startable.map((i) => i.id))">
        {{ t("start.button", { count: startable.length }) }}
      </v-btn>
      <span v-if="blocked" class="text-body-2" role="status" data-testid="normal-start-blocked">{{ blocked }}</span>
    </div>
    <p class="text-caption mt-2" data-testid="normal-start-demo">{{ t("start.demoNote") }}</p>
  </StepCard>
</template>
