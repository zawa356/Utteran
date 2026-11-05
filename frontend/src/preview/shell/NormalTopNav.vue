<script setup lang="ts">
import { mdiCogOutline, mdiMicrophoneOutline, mdiWrenchOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { NORMAL_SECTIONS, type NormalSection } from "../domain/layout";

/** Normal mode: three destinations in the app bar (no sidebar, the body keeps the full width). */
const { layout, layoutState, t } = usePreview();
const ICONS: Record<NormalSection, string> = { transcribe: mdiMicrophoneOutline, setup: mdiWrenchOutline, settings: mdiCogOutline };
const items = computed(() => NORMAL_SECTIONS.map((id) => ({ id, icon: ICONS[id], title: t(id === "transcribe" ? "nav.home" : `section.${id}`) })));
</script>

<template>
  <nav class="p-topnav" :aria-label="t('nav.label')" data-testid="top-nav">
    <v-btn
      v-for="item in items"
      :key="item.id"
      variant="text"
      size="small"
      :prepend-icon="item.icon"
      :color="layoutState.section === item.id ? 'primary' : undefined"
      :class="{ 'p-topnav__item--active': layoutState.section === item.id }"
      :aria-current="layoutState.section === item.id ? 'page' : undefined"
      :data-testid="`topnav-${item.id}`"
      @click="layout.navigate(item.id)"
    >
      {{ item.title }}
    </v-btn>
  </nav>
</template>
