<script setup lang="ts">
import { mdiUndoVariant } from "@mdi/js";
import { computed } from "vue";
import PaneHeading from "../components/PaneHeading.vue";
import { usePreview } from "../composables";
import { type InspectorTab, overrideCount } from "../domain/demo";
import PresetApply from "../settings/PresetApply.vue";
import SettingsEditor from "../settings/SettingsEditor.vue";

/**
 * Step 2 (centre pane): settings for the jobs — the common defaults, or individual values for the
 * files selected in step 1 (same count as the step-1 heading and the step-3 button). Iteration 4:
 * the editor has categories (ASR, prompt & glossary, diarization, output, advanced) and a preset
 * can be applied to the current target. The pane body scrolls; the target switch stays on top.
 */
const { demo, state, t } = usePreview();
const count = computed(() => state.value.selected.length);
const anyOverride = computed(() => state.value.selected.some((id) => overrideCount(state.value, id) > 0));
// The full name of a single selected input (the list may cut it).
const target = computed(() => {
  const sel = state.value.inputs.filter((i) => state.value.selected.includes(i.id));
  if (sel.length === 1) return sel[0]!.name;
  return sel.length > 1 ? t("inspector.targetMany", { count: sel.length }) : "";
});
</script>

<template>
  <div class="p-pane__body p-inspector">
    <PaneHeading id="pane-inspector-heading" :step="2" :title="t('expert.inspector')" :meta="t('inputs.selectedShort', { count })" />
    <v-btn-toggle
      :model-value="state.inspectorTab"
      mandatory
      divided
      variant="outlined"
      density="compact"
      color="primary"
      class="p-inspector__target"
      :aria-label="t('inspector.tabs')"
      data-testid="inspector-tabs"
      @update:model-value="(v: InspectorTab) => demo.setInspectorTab(v)"
    >
      <v-btn value="common" data-testid="inspector-tab-common">{{ t("inspector.common") }}</v-btn>
      <v-btn value="selection" data-testid="inspector-tab-selection">{{ t("inspector.selection", { count }) }}</v-btn>
    </v-btn-toggle>

    <template v-if="state.inspectorTab === 'common'">
      <div class="u-row">
        <span class="text-body-medium">{{ t("inspector.commonHelp") }}</span>
        <v-spacer />
        <v-btn variant="text" :prepend-icon="mdiUndoVariant" data-testid="common-reset" @click="demo.resetCommon()">{{ t("inspector.resetCommon") }}</v-btn>
      </div>
      <PresetApply target="common" />
      <SettingsEditor mode="common" />
    </template>
    <template v-else>
      <p v-if="count === 0" class="text-body-medium" data-testid="inspector-no-selection">{{ t("inspector.noSelection") }}</p>
      <template v-else>
        <div class="u-row">
          <span class="text-body-medium font-weight-medium p-target" :title="target" data-testid="inspector-target">{{ target }}</span>
          <v-spacer />
          <v-btn variant="text" :prepend-icon="mdiUndoVariant" :disabled="!anyOverride" data-testid="selection-reset-all" @click="demo.resetAllOverrides()">
            {{ t("inspector.resetAll") }}
          </v-btn>
        </div>
        <PresetApply target="selection" />
        <SettingsEditor mode="selection" />
      </template>
    </template>
  </div>
</template>
