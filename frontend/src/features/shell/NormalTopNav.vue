<script setup lang="ts">
import { mdiCheck, mdiMenuDown } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { NORMAL_SECTIONS, TOPNAV_FOLD_WIDTH } from "../../domain/shell";
import { SECTION_ICONS } from "./navIcons";

/**
 * Normal mode (Design Freeze): three destinations in the app bar, no sidebar. Below
 * TOPNAV_FOLD_WIDTH they fold into one menu button that names the current page — the labels are
 * never shrunk and the bar never scrolls sideways. Expert-only pages are not offered here.
 */
const { shell } = useCore();
const state = useStore(shell.store);
const { t } = useI18n();
const display = useDisplay();
const folded = computed(() => display.width.value < TOPNAV_FOLD_WIDTH);
const items = computed(() => NORMAL_SECTIONS.map((id) => ({ id, icon: SECTION_ICONS[id], title: t(`section.${id}`) })));
const current = computed(() => items.value.find((i) => i.id === state.value.section) ?? items.value[0]!);
</script>

<template>
  <nav class="u-topnav" :aria-label="t('nav.label')" data-testid="top-nav" :data-folded="folded">
    <v-menu v-if="folded" location="bottom start">
      <template #activator="{ props: menu }">
        <v-btn v-bind="menu" variant="text" :prepend-icon="current.icon" :append-icon="mdiMenuDown" :aria-label="t('nav.menu', { section: current.title })" data-testid="top-nav-menu">
          {{ current.title }}
        </v-btn>
      </template>
      <v-list :aria-label="t('nav.label')" data-testid="top-nav-list">
        <v-list-item
          v-for="item in items"
          :key="item.id"
          :prepend-icon="item.icon"
          :title="item.title"
          :active="state.section === item.id"
          color="primary"
          :aria-current="state.section === item.id ? 'page' : undefined"
          :data-testid="`nav-${item.id}`"
          @click="shell.navigate(item.id)"
        >
          <template v-if="state.section === item.id" #append><v-icon :icon="mdiCheck" aria-hidden="true" /></template>
        </v-list-item>
      </v-list>
    </v-menu>
    <template v-else>
      <v-btn
        v-for="item in items"
        :key="item.id"
        variant="text"
        :prepend-icon="item.icon"
        :color="state.section === item.id ? 'primary' : undefined"
        :class="{ 'u-topnav__item--active': state.section === item.id }"
        :aria-current="state.section === item.id ? 'page' : undefined"
        :data-testid="`nav-${item.id}`"
        @click="shell.navigate(item.id)"
      >
        {{ item.title }}
      </v-btn>
    </template>
  </nav>
</template>
