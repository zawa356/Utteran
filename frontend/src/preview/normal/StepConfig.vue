<script setup lang="ts">
import { computed } from "vue";
import SettingsFields from "../components/SettingsFields.vue";
import { usePreview } from "../composables";
import StepCard from "./StepCard.vue";

/**
 * "Recommended configuration": a one-line summary and three everyday choices. The model and
 * the execution route are behind "details"; the route is decided by the backend (B4/B7) and is
 * NOT fetched here, so nothing pretends to be a verified backend/device.
 */
const { state, t } = usePreview();
const overridden = computed(() => Object.keys(state.value.overrides).length);
const summary = computed(() => {
  const c = state.value.common;
  return [
    t(`model.${c.model}`),
    t(`lang.${c.language}`),
    c.diarization ? t("summary.diarizationOn", { speakers: c.speakers === "auto" ? t("speakers.auto") : t("speakers.n", { n: c.speakers }) }) : t("summary.diarizationOff"),
    c.formats.map((f) => f.toUpperCase()).join(" · "),
  ].join(" / ");
});
</script>

<template>
  <StepCard :n="2" :title="t('step.config')" testid="step-config">
    <div class="u-row mb-2">
      <span class="text-body-2" data-testid="config-summary">{{ t("config.recommended") }}: {{ summary }}</span>
      <v-chip size="x-small" label color="warning" variant="outlined" data-testid="config-unverified">{{ t("config.unverified") }}</v-chip>
    </div>
    <SettingsFields mode="common" :fields="['language', 'diarization', 'formats']" />
    <v-alert v-if="overridden > 0" type="info" variant="outlined" class="mt-2" data-testid="config-overrides-note">
      {{ t("config.overridesNote", { count: overridden }) }}
    </v-alert>
    <v-expansion-panels class="mt-2" variant="accordion" flat>
      <v-expansion-panel data-testid="config-details">
        <v-expansion-panel-title>{{ t("config.details") }}</v-expansion-panel-title>
        <v-expansion-panel-text>
          <div class="u-stack">
            <SettingsFields mode="common" :fields="['model', 'speakers']" />
            <v-alert type="info" variant="outlined" data-testid="config-route">{{ t("config.route") }}</v-alert>
          </div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </StepCard>
</template>
