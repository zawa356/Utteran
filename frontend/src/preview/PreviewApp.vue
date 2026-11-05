<script setup lang="ts">
import { watch } from "vue";
import { useTheme } from "vuetify";
import { usePreview } from "./composables";
import PreviewShell from "./PreviewShell.vue";

const { state } = usePreview();
const theme = useTheme();
watch(() => state.value.theme, (value) => void theme.change(value), { immediate: true });
watch(() => state.value.language, (value) => document.documentElement.setAttribute("lang", value), { immediate: true });
</script>

<template>
  <v-app data-testid="preview-ready">
    <PreviewShell />
  </v-app>
</template>
