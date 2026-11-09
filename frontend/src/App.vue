<script setup lang="ts">
import { onMounted, watch } from "vue";
import { useTheme } from "vuetify";
import { useCore, useI18n } from "./composables/core";
import { useStore } from "./composables/useStore";
import AppShell from "./features/shell/AppShell.vue";

const core = useCore();
const settings = useStore(core.settings.store);
const theme = useTheme();
const { language } = useI18n();

// Only the server-confirmed value is applied (a failed save never changes the look).
watch(
  () => settings.value.saved?.theme,
  (value) => {
    if (value) void theme.change(value);
  },
);
watch(language, (value) => document.documentElement.setAttribute("lang", value), { immediate: true });
onMounted(() => void core.start());
</script>

<template>
  <v-app data-testid="app-ready">
    <AppShell />
  </v-app>
</template>
