<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from "vue";
import { useTheme } from "vuetify";
import { usePreview } from "./composables";
import PreviewShell from "./PreviewShell.vue";
import { PREVIEW_THEMES } from "./theme";

const { state } = usePreview();
const theme = useTheme();
// "System" follows the OS setting for whichever palette (green proposal / UI-2 blue) is chosen.
const media = window.matchMedia("(prefers-color-scheme: dark)");
const systemDark = ref(media.matches);
const onMedia = (e: MediaQueryListEvent) => (systemDark.value = e.matches);
media.addEventListener("change", onMedia);
onScopeDispose(() => media.removeEventListener("change", onMedia));
const themeName = computed(() => {
  const dark = state.value.theme === "dark" || (state.value.theme === "system" && systemDark.value);
  return PREVIEW_THEMES[state.value.palette][dark ? "dark" : "light"];
});
watch(themeName, (value) => void theme.change(value), { immediate: true });
watch(() => state.value.language, (value) => document.documentElement.setAttribute("lang", value), { immediate: true });
</script>

<template>
  <v-app class="p-app" data-testid="preview-ready" :data-palette="state.palette">
    <PreviewShell />
  </v-app>
</template>
