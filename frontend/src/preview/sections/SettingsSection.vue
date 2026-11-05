<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import type { PreviewLanguage, PreviewTheme } from "../domain/demo";

/** Prototype settings: language and theme in memory only (production saves via S1). */
const { demo, state, t } = usePreview();
const themes = computed(() => (["system", "light", "dark"] as const).map((v) => ({ value: v, title: t(`theme.${v}`) })));
const languages = [
  { value: "ja", title: "日本語" },
  { value: "en", title: "English" },
];
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="settings-heading" data-testid="section-settings">
    <h1 id="settings-heading" class="u-heading">{{ t("section.settings") }}</h1>
    <v-alert type="info" variant="outlined">{{ t("settings.notSaved") }}</v-alert>
    <v-select :label="t('settings.theme')" :items="themes" :model-value="state.theme" class="u-w-sm" data-testid="settings-theme" @update:model-value="(v) => demo.setTheme(v as PreviewTheme)" />
    <v-select :label="t('settings.language')" :items="languages" :model-value="state.language" class="u-w-sm" data-testid="settings-language" @update:model-value="(v) => demo.setLanguage(v as PreviewLanguage)" />
    <p class="text-body-small">{{ t("settings.later") }}</p>
  </section>
</template>
