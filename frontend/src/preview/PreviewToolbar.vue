<script setup lang="ts">
import { mdiRestore } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "./composables";
import { type PreviewLanguage, type PreviewTheme, type Scenario, SCENARIOS, type SyncExample } from "./domain/demo";

/**
 * Reviewer controls, deliberately outside the product UI (a tinted strip under the banner):
 * display scenario, connection example, language, theme and a full reset.
 */
const { demo, state, t } = usePreview();
const scenarios = computed(() => SCENARIOS.map((s) => ({ value: s, title: t(`scenario.${s}`) })));
const syncs = computed(() => (["none", "synced", "resyncing", "offline"] as const).map((s) => ({ value: s, title: t(`syncOption.${s}`) })));
const themes = computed(() => (["system", "light", "dark"] as const).map((v) => ({ value: v, title: t(`theme.${v}`) })));
</script>

<template>
  <v-sheet class="p-toolbar mx-2 mb-2 pa-2" border rounded="sm" role="region" :aria-label="t('toolbar.label')" data-testid="prototype-toolbar">
    <span class="text-caption font-weight-medium">{{ t("toolbar.label") }}</span>
    <v-select
      :label="t('toolbar.scenario')"
      :items="scenarios"
      :model-value="state.scenario"
      class="p-toolbar__select"
      data-testid="toolbar-scenario"
      @update:model-value="(v) => demo.setScenario(v as Scenario)"
    />
    <v-select
      :label="t('toolbar.sync')"
      :items="syncs"
      :model-value="state.sync"
      class="p-toolbar__select"
      data-testid="toolbar-sync"
      @update:model-value="(v) => demo.setSync(v as SyncExample)"
    />
    <v-btn-toggle
      :model-value="state.language"
      mandatory
      density="compact"
      variant="outlined"
      divided
      :aria-label="t('settings.language')"
      data-testid="toolbar-language"
      @update:model-value="(v: PreviewLanguage) => demo.setLanguage(v)"
    >
      <v-btn value="ja" size="small" data-testid="lang-ja">日本語</v-btn>
      <v-btn value="en" size="small" data-testid="lang-en">English</v-btn>
    </v-btn-toggle>
    <v-btn-toggle
      :model-value="state.theme"
      mandatory
      density="compact"
      variant="outlined"
      divided
      :aria-label="t('settings.theme')"
      data-testid="toolbar-theme"
      @update:model-value="(v: PreviewTheme) => demo.setTheme(v)"
    >
      <v-btn v-for="th in themes" :key="th.value" :value="th.value" size="small" :data-testid="`theme-${th.value}`">{{ th.title }}</v-btn>
    </v-btn-toggle>
    <v-btn size="small" variant="text" :prepend-icon="mdiRestore" data-testid="toolbar-reset" @click="demo.reset()">{{ t("toolbar.reset") }}</v-btn>
  </v-sheet>
</template>
