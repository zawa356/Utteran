<script setup lang="ts">
import { mdiMenuClose, mdiMenuOpen } from "@mdi/js";
import { computed, nextTick, ref, useTemplateRef } from "vue";
import { useDisplay } from "vuetify";
import ResizeHandle from "../../components/ResizeHandle.vue";
import { SIDEBAR } from "../../domain/paneLayout";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { EXPERT_SECTIONS, isRail, NARROW_WINDOW, sectionStatus } from "../../domain/shell";
import { SECTION_ICONS } from "./navIcons";

/**
 * Expert management sidebar (Design Freeze): six destinations. It folds to an icon rail by the
 * button at its top; narrow windows start as a rail. The button stays in the rail, so the sidebar
 * can always be expanded again, at any width. In the rail every item keeps its name (aria-label
 * and tooltip). The v1 drawer is resizable for this session.
 */
const { shell, apiMode } = useCore();
const state = useStore(shell.store);
const { t } = useI18n();
const display = useDisplay();
const narrow = computed(() => display.width.value < NARROW_WINDOW);
const rail = computed(() => isRail(state.value, narrow.value));
const v1 = apiMode === "v1-dev";
const overlay = computed(() => v1 && !rail.value && display.width.value < 540);
const maxWidth = computed(() => Math.min(SIDEBAR.max, display.width.value - (overlay.value ? 32 : 260)));
const width = computed(() => v1 ? Math.max(SIDEBAR.min, Math.min(state.value.sidebarWidth, maxWidth.value)) : 232);
const navToggle = useTemplateRef("navToggle");
const dragging = ref(false);

async function closeOverlay() {
  if (!overlay.value) return;
  shell.toggleNav(true);
  await nextTick();
  (navToggle.value?.$el as HTMLElement | undefined)?.focus();
}
const items = computed(() =>
  EXPERT_SECTIONS.map((id) => {
    const title = t(`section.${id}`);
    const status = sectionStatus(id, apiMode) === "partial" ? t("status.partial") : t("status.notMigrated");
    return { id, title, status, icon: SECTION_ICONS[id], label: t("nav.itemWithStatus", { section: title, status }) };
  }),
);
</script>

<template>
  <v-navigation-drawer
    :permanent="!overlay"
    :temporary="overlay"
    :model-value="true"
    :rail="rail"
    :rail-width="SIDEBAR.rail"
    :width="width"
    :class="{ 'u-sidebar-dragging': dragging }"
    border="e"
    data-testid="sidebar"
    :data-rail="rail"
    @update:model-value="(open) => { if (!open) void closeOverlay(); }"
    @keydown.esc="closeOverlay"
  >
    <template #prepend>
      <div class="u-sidebar-head">
        <span v-if="!rail" class="u-subheading u-ellipsis">{{ t("nav.title") }}</span>
        <v-btn
          ref="navToggle"
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
          @click="shell.navigate(s.id); closeOverlay()"
        >
          <v-tooltip v-if="rail" activator="parent" location="end">{{ s.label }}</v-tooltip>
        </v-list-item>
      </v-list>
    </nav>
    <ResizeHandle
      v-if="v1 && !rail"
      class="u-sidebar-resize"
      :value="width"
      :min="SIDEBAR.min"
      :max="maxWidth"
      :label="t('nav.resize')"
      controls="u-sidebar-nav"
      testid="resize-sidebar"
      @resize="(px) => shell.resizeSidebar(Math.min(maxWidth, px))"
      @reset="shell.resizeSidebar(SIDEBAR.default)"
      @dragging="(active) => (dragging = active)"
    />
  </v-navigation-drawer>
</template>

<style scoped>
.u-sidebar-resize {
  position: absolute;
  inset: 0 0 0 auto;
  width: 8px;
}
.u-sidebar-dragging {
  transition: none;
}
</style>
