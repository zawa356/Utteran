<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import { useTheme } from "vuetify";
import { usePreview } from "./composables";
import PreviewShell from "./PreviewShell.vue";
import { PREVIEW_THEMES } from "./theme";

const { state } = usePreview();
const theme = useTheme();
// Appearance (system / light / dark) and accent (green / blue) are independent (owner memo #22).
const media = window.matchMedia("(prefers-color-scheme: dark)");
const systemDark = ref(media.matches);
const onMedia = (e: MediaQueryListEvent) => (systemDark.value = e.matches);
media.addEventListener("change", onMedia);
onScopeDispose(() => media.removeEventListener("change", onMedia));
const themeName = computed(() => {
  const dark = state.value.appearance === "dark" || (state.value.appearance === "system" && systemDark.value);
  return PREVIEW_THEMES[state.value.accentTheme][dark ? "dark" : "light"];
});
watch(themeName, (value) => void theme.change(value), { immediate: true });
watch(() => state.value.language, (value) => document.documentElement.setAttribute("lang", value), { immediate: true });
</script>

<template>
  <v-app class="p-app" data-testid="preview-ready" :data-accent="state.accentTheme" :data-theme-name="themeName">
    <PreviewShell />
  </v-app>
</template>
