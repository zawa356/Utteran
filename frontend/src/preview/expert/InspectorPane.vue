<script setup lang="ts">
import { mdiUndoVariant } from "@mdi/js";
import { computed } from "vue";
import SettingsFields from "../components/SettingsFields.vue";
import { usePreview } from "../composables";
import { type InspectorTab, selectionField } from "../domain/demo";
import { SETTING_KEYS } from "../domain/fixtures";
import EnvironmentSummary from "../environment/EnvironmentSummary.vue";

/** Centre pane: common defaults vs. the selected inputs, with inherit/override per field. */
const { demo, state, t } = usePreview();
const anyOverride = computed(() => SETTING_KEYS.some((k) => selectionField(state.value, k).coverage !== "none"));
const STAGES = ["input", "asr", "diarization", "output"] as const;
// The full name of a single selected input (the list may cut it).
const target = computed(() => {
  const sel = state.value.inputs.filter((i) => state.value.selected.includes(i.id));
  if (sel.length === 1) return sel[0]!.name;
  return sel.length > 1 ? t("inspector.targetMany", { count: sel.length }) : "";
});
</script>

<template>
  <div class="p-pane__body">
    <div class="p-pane__title">
      <h2 class="u-subheading">{{ t("expert.inspector") }}</h2>
    </div>
    <v-tabs
      :model-value="state.inspectorTab"
      density="compact"
      color="primary"
      :aria-label="t('inspector.tabs')"
      data-testid="inspector-tabs"
      @update:model-value="(v) => demo.setInspectorTab(v as InspectorTab)"
    >
      <v-tab value="common" data-testid="inspector-tab-common">{{ t("inspector.common") }}</v-tab>
      <v-tab value="selection" data-testid="inspector-tab-selection">{{ t("inspector.selection", { count: state.selected.length }) }}</v-tab>
    </v-tabs>

    <template v-if="state.inspectorTab === 'common'">
      <div class="u-row">
        <span class="text-body-medium">{{ t("inspector.commonHelp") }}</span>
        <v-spacer />
        <v-btn size="small" variant="text" :prepend-icon="mdiUndoVariant" data-testid="common-reset" @click="demo.resetCommon()">{{ t("inspector.resetCommon") }}</v-btn>
      </div>
      <SettingsFields mode="common" />
    </template>
    <template v-else>
      <v-alert v-if="state.selected.length === 0" type="info" variant="outlined" data-testid="inspector-no-selection">{{ t("inspector.noSelection") }}</v-alert>
      <template v-else>
        <div class="text-body-medium font-weight-medium p-target" :title="target" data-testid="inspector-target">{{ target }}</div>
        <div class="u-row">
          <span class="text-body-medium" data-testid="inspector-selection-help">{{ t("inspector.selectionHelp", { count: state.selected.length }) }}</span>
          <v-spacer />
          <v-btn size="small" variant="text" :prepend-icon="mdiUndoVariant" :disabled="!anyOverride" data-testid="selection-reset-all" @click="demo.resetAllOverrides()">
            {{ t("inspector.resetAll") }}
          </v-btn>
        </div>
        <SettingsFields mode="selection" />
      </template>
    </template>

    <v-divider />
    <div class="u-row" :aria-label="t('inspector.pipeline')" role="group">
      <span class="text-body-medium font-weight-medium">{{ t("inspector.pipeline") }}</span>
      <template v-for="(s, i) in STAGES" :key="s">
        <span v-if="i > 0" aria-hidden="true">→</span>
        <v-chip size="x-small" label variant="outlined">{{ t(`pipeline.${s}`) }}</v-chip>
      </template>
    </div>
    <EnvironmentSummary />
    <p class="text-body-small" data-testid="inspector-route">{{ t("inspector.routeNote") }}</p>
    <p class="text-body-small" data-testid="inspector-prompt-later">{{ t("inspector.promptLater") }}</p>
  </div>
</template>
