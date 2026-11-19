<script setup lang="ts">
import { mdiMenuClose, mdiMenuOpen } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { EXPERT_SECTIONS, isRail, NARROW_WINDOW, SECTION_STATUS } from "../../domain/shell";
import { SECTION_ICONS } from "./navIcons";

/**
 * Expert management sidebar (Design Freeze): six destinations. It folds to an icon rail by the
 * button at its top; narrow windows start as a rail. The button stays in the rail, so the sidebar
 * can always be expanded again, at any width. In the rail every item keeps its name (aria-label
 * and tooltip). Width dragging is bundle D4.
 */
const { shell } = useCore();
const state = useStore(shell.store);
const { t } = useI18n();
const display = useDisplay();
const narrow = computed(() => display.width.value < NARROW_WINDOW);
const rail = computed(() => isRail(state.value, narrow.value));
const items = computed(() =>
  EXPERT_SECTIONS.map((id) => {
    const title = t(`section.${id}`);
    const status = SECTION_STATUS[id] === "partial" ? t("status.partial") : t("status.notMigrated");
    return { id, title, status, icon: SECTION_ICONS[id], label: t("nav.itemWithStatus", { section: title, status }) };
  }),
);
</script>

<template>
  <v-navigation-drawer permanent :rail="rail" rail-width="56" width="232" border="e" data-testid="sidebar" :data-rail="rail">
    <template #prepend>
      <div class="u-sidebar-head">
        <span v-if="!rail" class="u-subheading u-ellipsis">{{ t("nav.title") }}</span>
        <v-btn
          variant="text"
          size="small"
          :icon="rail ? mdiMenuClose : mdiMenuOpen"
          :aria-label="rail ? t('nav.expand') : t('nav.collapse')"
          :aria-expanded="!rail"
          aria-controls="u-sidebar-nav"
          :title="rail ? t('nav.expand') : t('nav.collapse')"
          data-testid="nav-toggle"
          @click="shell.toggleNav(narrow)"
        />
      </div>
    </template>
    <nav id="u-sidebar-nav" :aria-label="t('nav.label')">
      <v-list nav data-testid="sidebar-list">
        <v-list-item
          v-for="s in items"
          :key="s.id"
          :active="s.id === state.section"
          color="primary"
          :prepend-icon="s.icon"
          :title="s.title"
          :subtitle="rail ? undefined : s.status"
          :aria-current="s.id === state.section ? 'page' : undefined"
          :aria-label="rail ? s.label : undefined"
          :data-testid="`nav-${s.id}`"
          @click="shell.navigate(s.id)"
        >
          <v-tooltip v-if="rail" activator="parent" location="end">{{ s.label }}</v-tooltip>
        </v-list-item>
      </v-list>
    </nav>
  </v-navigation-drawer>
</template>
