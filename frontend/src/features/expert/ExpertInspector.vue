<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "../../composables/core";
import { useV1 } from "../../composables/v1";
import { summarize } from "../../domain/v1/settings";
import type { MessageKey } from "../../i18n";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";
import InputOverrides from "../transcribe/InputOverrides.vue";
import PresetManager from "../transcribe/PresetManager.vue";
import RowGlossary from "../transcribe/RowGlossary.vue";
import V1BasicSettings from "../transcribe/V1BasicSettings.vue";
import V1LibrarySettings from "../transcribe/V1LibrarySettings.vue";
import V1OutputFolder from "../transcribe/V1OutputFolder.vue";

/**
 * Expert ② processing settings in the Design Freeze shape (#96, iteration 4 memo #19 / iteration 5):
 * a switch between the common defaults and the files selected in ①, and five categories
 * (音声認識 / プロンプト・用語集 / 話者分離 / 出力 / 詳細). It only arranges the existing v1 parts:
 * common = the Preset/Policy revisions and the screen's B5 `common_overrides`; selection = the
 * per-file overrides (staged → Apply) and the per-file glossary. What the v1 path cannot express
 * (initial prompt, diarization) is stated as unsupported instead of offered as a control.
 * The target and the category are view state of this screen only (kept while Expert is cached).
 */
type Target = "common" | "selection";
const CATEGORIES = ["asr", "prompt", "diarization", "output", "advanced"] as const;
type Category = (typeof CATEGORIES)[number];

const { library, inputs } = useV1();
const { t } = useI18n();
const selected = computed(() => inputs.value.rows.filter((r) => r.selected).length);
const target = ref<Target>(selected.value > 0 ? "selection" : "common");
const category = ref<Category>("asr");
// Selecting files in ① shows their settings (the "selected n" target); nothing changes values.
watch(selected, (now, before) => {
  if (now > 0 && before === 0) target.value = "selection";
  if (now === 0) target.value = "common";
});
const preset = computed(() => library.value.preset.item);
const presetSummary = computed(() => (preset.value ? summarize(preset.value.settings) : null));
const overrideCount = computed(() => inputs.value.rows.filter((r) => r.selected && (Object.values(r.overrides).some((v) => v !== undefined) || r.output.kind === "custom")).length);
</script>

<template>
  <div class="u-inspector" data-testid="expert-inspector" :data-target="target" :data-category="category">
    <v-btn-toggle
      :model-value="target"
      mandatory
      divided
      variant="outlined"
      density="compact"
      color="primary"
      class="u-inspector__target"
      :aria-label="t('ui3.inspector.target')"
      data-testid="inspector-tabs"
      @update:model-value="(v: Target) => (target = v)"
    >
      <v-btn value="common" data-testid="inspector-tab-common">{{ t("ui3.inspector.common") }}</v-btn>
      <v-btn value="selection" data-testid="inspector-tab-selection">{{ t("ui3.inspector.selection", { count: selected }) }}</v-btn>
    </v-btn-toggle>
    <p class="text-body-2 mt-2" data-testid="inspector-help">
      {{ target === "common" ? t("ui3.inspector.commonHelp") : t("ui3.inspector.selectionHelp", { count: selected, overridden: overrideCount }) }}
    </p>

    <!-- A wrapping row of buttons rather than scrolling tabs: every category stays visible in a narrow pane. -->
    <div class="u-cats" role="group" :aria-label="t('ui3.cat.label')" data-testid="category-tabs">
      <v-btn
        v-for="c in CATEGORIES"
        :key="c"
        variant="text"
        :color="c === category ? 'primary' : undefined"
        :class="{ 'u-cat--active': c === category }"
        :aria-pressed="c === category"
        :data-testid="`category-${c}`"
        @click="category = c"
      >
        {{ t(`ui3.cat.${c}` as MessageKey) }}
      </v-btn>
    </div>

    <div class="u-cat-body u-stack" role="region" :aria-label="t(`ui3.cat.${category}` as MessageKey)" :data-testid="`category-panel-${category}`">
      <!-- common defaults -->
      <template v-if="target === 'common'">
        <template v-if="category === 'asr'">
          <V1BasicSettings :fields="['language', 'model']" />
          <p class="text-caption" data-testid="inspector-engine-note">{{ t("ui3.inspector.engineCommon") }}</p>
        </template>
        <template v-else-if="category === 'prompt'">
          <p class="text-body-2" data-testid="inspector-prompt-unsupported">{{ t("ui3.inspector.promptUnsupported") }}</p>
          <template v-if="preset && presetSummary">
            <p class="text-body-2" data-testid="inspector-common-glossary">{{ t("ui3.inspector.commonGlossary", { count: presetSummary.glossary, name: preset.name }) }}</p>
            <PresetManager :preset="preset" />
          </template>
          <p v-else class="text-body-2">{{ t("ui3.settings.needPreset") }}</p>
        </template>
        <template v-else-if="category === 'diarization'">
          <V1BasicSettings :fields="['diarization']" />
        </template>
        <template v-else-if="category === 'output'">
          <V1BasicSettings :fields="['formats']" />
          <V1OutputFolder />
        </template>
        <template v-else>
          <V1LibrarySettings />
          <EnvironmentPanel detail />
        </template>
      </template>

      <!-- the files selected in ① -->
      <template v-else>
        <p v-if="selected === 0" class="text-body-2" data-testid="inspector-no-selection">{{ t("ui3.inspector.noSelection") }}</p>
        <template v-else-if="category === 'asr'">
          <InputOverrides bare :fields="['engine', 'language']" />
        </template>
        <template v-else-if="category === 'prompt'">
          <p class="text-body-2" data-testid="inspector-prompt-unsupported">{{ t("ui3.inspector.promptUnsupported") }}</p>
          <RowGlossary />
        </template>
        <template v-else-if="category === 'diarization'">
          <V1BasicSettings :fields="['diarization']" />
        </template>
        <template v-else-if="category === 'output'">
          <InputOverrides bare :fields="['formats', 'output']" />
        </template>
        <template v-else>
          <p class="text-body-2" data-testid="inspector-advanced-none">{{ t("ui3.inspector.advancedNone") }}</p>
          <p class="text-caption" data-testid="v1-overrides-note">{{ t("v1.overrides.note") }}</p>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.u-inspector__target {
  max-width: 100%;
}
.u-inspector__target :deep(.v-btn) {
  min-width: 0;
}
.u-cats {
  position: sticky;
  top: 44px; /* below the sticky pane title (10 + 24 + 8 px + border) */
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  margin-top: 12px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  background: rgb(var(--v-theme-surface));
}
.u-cats :deep(.v-btn) {
  padding: 0 10px;
  font-size: 0.9375rem;
}
.u-cat--active {
  box-shadow: inset 0 -2px 0 rgb(var(--v-theme-primary));
  font-weight: 600;
}
.u-cat-body {
  padding-top: 12px;
}
</style>
