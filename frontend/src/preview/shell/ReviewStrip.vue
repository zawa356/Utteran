<script setup lang="ts">
import { mdiChevronDown, mdiChevronUp, mdiRestore } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { type PreviewLanguage, type PreviewPalette, type PreviewTheme, type Scenario, SCENARIOS, type SyncExample } from "../domain/demo";
import { ENV_EXAMPLES, type EnvExample } from "../domain/envExamples";
import type { PanePreset } from "../domain/layout";

/**
 * Reviewer controls, deliberately OUTSIDE the product UI: a dashed, tinted strip docked at the
 * bottom of the window (collapsible). Display scenario, fictional environment example, connection
 * example, colour proposal, theme, language, Expert pane split examples and resets.
 */
const { demo, layout, layoutState, state, t } = usePreview();
const scenarios = computed(() => SCENARIOS.map((s) => ({ value: s, title: t(`scenario.${s}`) })));
const envs = computed(() => ENV_EXAMPLES.map((e) => ({ value: e, title: t(`envExample.${e}`) })));
const syncs = computed(() => (["none", "synced", "resyncing", "offline"] as const).map((s) => ({ value: s, title: t(`syncOption.${s}`) })));
const themes = computed(() => (["system", "light", "dark"] as const).map((v) => ({ value: v, title: t(`theme.${v}`) })));
const open = computed(() => layoutState.value.reviewStripOpen);
</script>

<template>
  <v-footer app class="p-review" role="region" :aria-label="t('toolbar.label')" data-testid="prototype-toolbar">
    <div class="p-review__row">
      <v-btn
        size="small"
        variant="text"
        :append-icon="open ? mdiChevronDown : mdiChevronUp"
        :aria-expanded="open"
        aria-controls="p-review-controls"
        data-testid="toolbar-toggle"
        @click="layout.toggleReviewStrip()"
      >
        {{ t("toolbar.label") }}
      </v-btn>
      <div v-show="open" id="p-review-controls" class="p-review__controls">
        <v-select :label="t('toolbar.scenario')" :items="scenarios" :model-value="state.scenario" class="p-review__select" data-testid="toolbar-scenario" @update:model-value="(v) => demo.setScenario(v as Scenario)" />
        <v-select :label="t('toolbar.env')" :items="envs" :model-value="state.envExample" class="p-review__select p-review__select--sm" data-testid="toolbar-env" @update:model-value="(v) => demo.setEnvExample(v as EnvExample)" />
        <v-select :label="t('toolbar.sync')" :items="syncs" :model-value="state.sync" class="p-review__select p-review__select--sm" data-testid="toolbar-sync" @update:model-value="(v) => demo.setSync(v as SyncExample)" />
        <v-btn-toggle :model-value="state.palette" mandatory density="compact" variant="outlined" divided :aria-label="t('toolbar.palette')" data-testid="toolbar-palette" @update:model-value="(v: PreviewPalette) => demo.setPalette(v)">
          <v-btn value="green" size="small" data-testid="palette-green">{{ t("palette.green") }}</v-btn>
          <v-btn value="ui2" size="small" data-testid="palette-ui2">{{ t("palette.ui2") }}</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="state.theme" mandatory density="compact" variant="outlined" divided :aria-label="t('settings.theme')" data-testid="toolbar-theme" @update:model-value="(v: PreviewTheme) => demo.setTheme(v)">
          <v-btn v-for="th in themes" :key="th.value" :value="th.value" size="small" :data-testid="`theme-${th.value}`">{{ th.title }}</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="state.language" mandatory density="compact" variant="outlined" divided :aria-label="t('settings.language')" data-testid="toolbar-language" @update:model-value="(v: PreviewLanguage) => demo.setLanguage(v)">
          <v-btn value="ja" size="small" data-testid="lang-ja">日本語</v-btn>
          <v-btn value="en" size="small" data-testid="lang-en">English</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="null" density="compact" variant="outlined" divided :aria-label="t('toolbar.panes')" data-testid="toolbar-panes" @update:model-value="(v: PanePreset | null) => v && layout.applyPreset(v)">
          <v-btn value="standard" size="small" data-testid="preset-standard">{{ t("preset.standard") }}</v-btn>
          <v-btn value="wideInspector" size="small" data-testid="preset-wide-inspector">{{ t("preset.wideInspector") }}</v-btn>
        </v-btn-toggle>
        <v-btn size="small" variant="text" :prepend-icon="mdiRestore" data-testid="toolbar-reset" @click="demo.reset()">{{ t("toolbar.reset") }}</v-btn>
      </div>
    </div>
  </v-footer>
</template>
