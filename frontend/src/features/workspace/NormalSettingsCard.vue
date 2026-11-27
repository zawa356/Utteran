<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "../../composables/core";
import { useV1 } from "../../composables/v1";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import V1BasicSettings from "../transcribe/V1BasicSettings.vue";
import V1LibrarySettings from "../transcribe/V1LibrarySettings.vue";

/**
 * Design Freeze Normal "設定" card (#96): language, diarization and formats first; the model and
 * the internal management (Preset/Policy revisions, environment) behind "details". The details
 * open by themselves only while something there is NEEDED to start or needs attention (no
 * Preset/Policy chosen, a missing or newer revision, a failed list) — they never close on their
 * own, and nothing is created, adopted or saved implicitly.
 */
const { library } = useV1();
const { t } = useI18n();

const needsLibrary = computed(() => {
  const l = library.value;
  return !l.preset.item || !l.policy.item || l.preset.missing || l.policy.missing
    || l.preset.newerRevision !== null || l.policy.newerRevision !== null
    || l.presets.phase === "error" || l.policies.phase === "error"
    || l.preset.phase === "error" || l.policy.phase === "error";
});
// Decide only once the lists and a restored selection have answered: at mount nothing is loaded
// yet, and an empty selection then is not a reason to open.
const settled = computed(() => {
  const l = library.value;
  return l.presets.phase !== "idle" && l.presets.phase !== "pending" && l.policies.phase !== "idle" && l.policies.phase !== "pending"
    && l.preset.phase !== "pending" && l.policy.phase !== "pending";
});
const open = ref<string[]>([]);
watch([needsLibrary, settled], ([needed, ready]) => {
  if (needed && ready && !open.value.includes("details")) open.value = [...open.value, "details"];
}, { immediate: true });
</script>

<template>
  <v-card class="u-fcard" tag="section" aria-labelledby="normal-settings-heading" data-testid="normal-settings">
    <div class="u-fcard__head"><h2 id="normal-settings-heading" class="u-fcard__title">{{ t("ui3.normal.settings") }}</h2></div>
    <V1BasicSettings />
    <v-expansion-panels v-model="open" multiple variant="accordion" flat class="u-fold">
      <v-expansion-panel value="details" data-testid="normal-details" :data-needed="needsLibrary">
        <v-expansion-panel-title data-testid="normal-details-toggle">
          {{ t("ui3.normal.details") }}
          <v-chip v-if="needsLibrary" size="x-small" color="warning" variant="tonal" class="ml-2" data-testid="normal-details-needed">{{ t("ui3.normal.detailsNeeded") }}</v-chip>
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <div class="u-stack">
            <V1BasicSettings :fields="['model']" />
            <v-divider />
            <h3 class="u-subheading">{{ t("ui3.normal.library") }}</h3>
            <V1LibrarySettings />
            <v-divider />
            <EnvironmentPanel />
          </div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>
