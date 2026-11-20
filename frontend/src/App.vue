<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from "vue";
import { useTheme } from "vuetify";
import { useCore, useI18n } from "./composables/core";
import { useStore } from "./composables/useStore";
import AppShell from "./features/shell/AppShell.vue";

const core = useCore();
const settings = useStore(core.settings.store);
const theme = useTheme();
const { language } = useI18n();

const v1 = core.apiMode === "v1-dev";
const shell = useStore(core.shell.store);
const systemDark = ref(false);
if (v1) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  systemDark.value = media.matches;
  const update = (event: MediaQueryListEvent) => { systemDark.value = event.matches; };
  media.addEventListener("change", update);
  onScopeDispose(() => media.removeEventListener("change", update));
}

// Appearance is server-confirmed. Accent applies immediately for this application session.
const themeName = computed(() => {
  const appearance = settings.value.saved?.theme ?? "system";
  if (!v1 || shell.value.accent === "blue") return appearance;
  return appearance === "dark" || (appearance === "system" && systemDark.value) ? "green-dark" : "green-light";
});
watch(themeName, (name) => void theme.change(name), { immediate: true });
watch(language, (value) => document.documentElement.setAttribute("lang", value), { immediate: true });
onMounted(() => void core.start());
</script>

<template>
  <v-app data-testid="app-ready" :data-api-mode="core.apiMode" :data-accent="v1 ? shell.accent : undefined">
    <AppShell />
  </v-app>
</template>
