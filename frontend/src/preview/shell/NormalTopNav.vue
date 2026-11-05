<script setup lang="ts">
import { mdiCheck, mdiCogOutline, mdiMenuDown, mdiMicrophoneOutline, mdiWrenchOutline } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import { usePreview } from "../composables";
import { NORMAL_SECTIONS, type NormalSection, TOPNAV_MIN_WIDTH } from "../domain/layout";

/**
 * Normal mode: three destinations in the app bar (no sidebar). In a narrow window (or at a large
 * zoom) they fold into one menu button that names the current page — the text is never shrunk.
 */
const { layout, layoutState, t } = usePreview();
const display = useDisplay();
const folded = computed(() => display.width.value < TOPNAV_MIN_WIDTH);
const ICONS: Record<NormalSection, string> = { transcribe: mdiMicrophoneOutline, setup: mdiWrenchOutline, settings: mdiCogOutline };
const items = computed(() => NORMAL_SECTIONS.map((id) => ({ id, icon: ICONS[id], title: t(id === "transcribe" ? "nav.home" : `section.${id}`) })));
const current = computed(() => items.value.find((i) => i.id === layoutState.value.section) ?? items.value[0]!);
</script>

<template>
  <nav class="p-topnav" :aria-label="t('nav.label')" data-testid="top-nav" :data-folded="folded">
    <v-menu v-if="folded" location="bottom start">
      <template #activator="{ props: menu }">
        <v-btn v-bind="menu" variant="text" :prepend-icon="current.icon" :append-icon="mdiMenuDown" data-testid="topnav-folded">{{ current.title }}</v-btn>
      </template>
      <v-list :aria-label="t('nav.label')">
        <v-list-item
          v-for="item in items"
          :key="item.id"
          :prepend-icon="item.icon"
          :title="item.title"
          :active="layoutState.section === item.id"
          color="primary"
          :data-testid="`topnav-${item.id}`"
          @click="layout.navigate(item.id)"
        >
          <template v-if="layoutState.section === item.id" #append><v-icon :icon="mdiCheck" aria-hidden="true" /></template>
        </v-list-item>
      </v-list>
    </v-menu>
    <template v-else>
      <v-btn
        v-for="item in items"
        :key="item.id"
        variant="text"
        :prepend-icon="item.icon"
        :color="layoutState.section === item.id ? 'primary' : undefined"
        :class="{ 'p-topnav__item--active': layoutState.section === item.id }"
        :aria-current="layoutState.section === item.id ? 'page' : undefined"
        :data-testid="`topnav-${item.id}`"
        @click="layout.navigate(item.id)"
      >
        {{ item.title }}
      </v-btn>
    </template>
  </nav>
</template>
