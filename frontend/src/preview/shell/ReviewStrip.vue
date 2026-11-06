<script setup lang="ts">
import { mdiChevronDown, mdiChevronUp, mdiRestore } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { type AccentTheme, type Appearance, APPEARANCES, type PreviewLanguage, type Scenario, SCENARIOS, SYNC_EXAMPLES, type SyncExample } from "../domain/demo";
import { ENV_EXAMPLES, type EnvExample } from "../domain/envExamples";
import { type MenuStyle, PANE_PRESET_IDS, type PanePreset } from "../domain/layout";
import { SETUP_EXAMPLES, type SetupExample } from "../domain/setup";

/**
 * Reviewer controls, deliberately OUTSIDE the product UI: a dashed, tinted strip docked at the
 * bottom of the window (collapsible). Display scenario, setup state example (iteration 4), fictional
 * environment example, job-status fetch example, accent theme, appearance, language, Expert pane
 * split examples, app-menu style and reset. The product's own theme setting is on the Settings page.
 */
const { demo, layout, layoutState, setup, setupState, state, t } = usePreview();
const setups = computed(() => SETUP_EXAMPLES.map((s) => ({ value: s, title: t(`setupExample.${s}`) })));
const scenarios = computed(() => SCENARIOS.map((s) => ({ value: s, title: t(`scenario.${s}`) })));
const envs = computed(() => ENV_EXAMPLES.map((e) => ({ value: e, title: t(`envExample.${e}`) })));
const syncs = computed(() => SYNC_EXAMPLES.map((s) => ({ value: s, title: t(`syncOption.${s}`) })));
const presets = computed(() => PANE_PRESET_IDS.map((p) => ({ value: p, title: t(`preset.${p}`) })));
const themes = computed(() => APPEARANCES.map((v) => ({ value: v, title: t(`theme.${v}`) })));
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
        <v-select :label="t('toolbar.scenario')" :items="scenarios" :model-value="state.scenario" class="p-review__select" :menu-props="{ maxHeight: 600 }" :list-props="{ density: 'compact' }" data-testid="toolbar-scenario" @update:model-value="(v) => demo.setScenario(v as Scenario)" />
        <v-select :label="t('toolbar.setup')" :items="setups" :model-value="setupState.example" class="p-review__select p-review__select--sm" data-testid="toolbar-setup" @update:model-value="(v) => setup.setExample(v as SetupExample)" />
        <v-select :label="t('toolbar.env')" :items="envs" :model-value="state.envExample" class="p-review__select p-review__select--sm" data-testid="toolbar-env" @update:model-value="(v) => demo.setEnvExample(v as EnvExample)" />
        <v-select :label="t('toolbar.sync')" :items="syncs" :model-value="state.sync" class="p-review__select p-review__select--sm" data-testid="toolbar-sync" @update:model-value="(v) => demo.setSync(v as SyncExample)" />
        <v-btn-toggle :model-value="state.accentTheme" mandatory density="compact" variant="outlined" divided :aria-label="t('toolbar.palette')" data-testid="toolbar-palette" @update:model-value="(v: AccentTheme) => demo.setAccentTheme(v)">
          <v-btn value="green" size="small" data-testid="palette-green">{{ t("accent.green") }}</v-btn>
          <v-btn value="blue" size="small" data-testid="palette-blue">{{ t("accent.blue") }}</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="state.appearance" mandatory density="compact" variant="outlined" divided :aria-label="t('settings.theme')" data-testid="toolbar-theme" @update:model-value="(v: Appearance) => demo.setAppearance(v)">
          <v-btn v-for="th in themes" :key="th.value" :value="th.value" size="small" :data-testid="`theme-${th.value}`">{{ th.title }}</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="state.language" mandatory density="compact" variant="outlined" divided :aria-label="t('settings.language')" data-testid="toolbar-language" @update:model-value="(v: PreviewLanguage) => demo.setLanguage(v)">
          <v-btn value="ja" size="small" data-testid="lang-ja">日本語</v-btn>
          <v-btn value="en" size="small" data-testid="lang-en">English</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="null" density="compact" variant="outlined" divided :aria-label="t('toolbar.panes')" data-testid="toolbar-panes" @update:model-value="(v: PanePreset | null) => v && layout.applyPreset(v)">
          <v-btn v-for="p in presets" :key="p.value" :value="p.value" size="small" :data-testid="`preset-${p.value}`">{{ p.title }}</v-btn>
        </v-btn-toggle>
        <v-btn-toggle :model-value="layoutState.menuStyle" mandatory density="compact" variant="outlined" divided :aria-label="t('toolbar.menu')" data-testid="toolbar-menu" @update:model-value="(v: MenuStyle) => layout.setMenuStyle(v)">
          <v-btn value="bar" size="small" data-testid="menustyle-bar">{{ t("menuStyle.bar") }}</v-btn>
          <v-btn value="button" size="small" data-testid="menustyle-button">{{ t("menuStyle.button") }}</v-btn>
        </v-btn-toggle>
        <v-btn size="small" variant="text" :prepend-icon="mdiRestore" data-testid="toolbar-reset" @click="demo.reset()">{{ t("toolbar.reset") }}</v-btn>
      </div>
    </div>
  </v-footer>
</template>
