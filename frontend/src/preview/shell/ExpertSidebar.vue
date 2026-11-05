<script setup lang="ts">
import {
  mdiCogOutline,
  mdiFormatListBulleted,
  mdiHistory,
  mdiMenuOpen,
  mdiMenuClose,
  mdiMicrophoneOutline,
  mdiPackageVariantClosed,
  mdiWrenchOutline,
} from "@mdi/js";
import { computed, ref } from "vue";
import { useDisplay } from "vuetify";
import ResizeHandle from "../components/ResizeHandle.vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";
import { EXPERT_SECTIONS, NARROW_WINDOW, SIDEBAR, type SectionId } from "../domain/layout";

/**
 * Expert management sidebar: six destinations, resizable (drag the right edge, or focus it and use
 * ←/→, Enter = reset), collapsible to an icon rail from the TOP of the sidebar. The toggle stays
 * visible in the rail, so the sidebar can always be expanded again — also in narrow windows,
 * which start as a rail. Iteration 3: no connection/sync indicator here (owner memo #11) — a local
 * app does not show its backend link; the status of jobs is reported where jobs are shown.
 */
const { layout, layoutState, state, t } = usePreview();
const display = useDisplay();
const narrow = computed(() => display.width.value < NARROW_WINDOW);
const rail = computed(() => (narrow.value ? !layoutState.value.sidebarExpandedNarrow : layoutState.value.sidebarCollapsed));
const dragging = ref(false);
// Never wider than 40 % of the window, so the work area keeps its space.
const maxWidth = computed(() => Math.max(SIDEBAR.min, Math.min(SIDEBAR.max, Math.floor(display.width.value * 0.4))));
const width = computed(() => Math.min(layoutState.value.sidebarWidth, maxWidth.value));

const ICONS: Record<SectionId, string> = {
  transcribe: mdiMicrophoneOutline,
  queue: mdiFormatListBulleted,
  results: mdiHistory,
  models: mdiPackageVariantClosed,
  setup: mdiWrenchOutline,
  settings: mdiCogOutline,
};
const activeJobs = computed(() => state.value.jobs.filter(isActive).length);
const items = computed(() => EXPERT_SECTIONS.map((id) => ({ id, icon: ICONS[id], title: t(`section.${id}`) })));
</script>

<template>
  <v-navigation-drawer
    permanent
    :rail="rail"
    :rail-width="SIDEBAR.rail"
    :width="width"
    border="e"
    class="p-sidebar"
    :class="{ 'p-no-transition': dragging }"
    data-testid="sidebar"
    :data-rail="rail"
  >
    <template #prepend>
      <div class="p-sidebar__head">
        <span v-if="!rail" class="text-body-medium font-weight-medium p-ellipsis">{{ t("nav.title") }}</span>
        <v-btn
          variant="text"
          size="small"
          :icon="rail ? mdiMenuClose : mdiMenuOpen"
          :aria-label="rail ? t('nav.expand') : t('nav.collapse')"
          :aria-expanded="!rail"
          aria-controls="p-sidebar-nav"
          :title="rail ? t('nav.expand') : t('nav.collapse')"
          data-testid="nav-toggle"
          @click="layout.toggleSidebar(narrow)"
        />
      </div>
    </template>
    <nav id="p-sidebar-nav" :aria-label="t('nav.label')">
      <v-list nav data-testid="sidebar-list">
        <v-list-item
          v-for="s in items"
          :key="s.id"
          :active="s.id === layoutState.section"
          color="primary"
          :prepend-icon="s.icon"
          :title="s.title"
          :aria-current="s.id === layoutState.section ? 'page' : undefined"
          :aria-label="rail ? s.title : undefined"
          :data-testid="`nav-${s.id}`"
          @click="layout.navigate(s.id)"
        >
          <template v-if="s.id === 'queue' && activeJobs > 0 && !rail" #append>
            <v-chip label color="primary" variant="tonal" :aria-label="t('nav.queueActive', { count: activeJobs })" data-testid="nav-queue-count">{{ activeJobs }}</v-chip>
          </template>
        </v-list-item>
      </v-list>
    </nav>
    <ResizeHandle
      v-if="!rail"
      class="p-sidebar__resize"
      :value="width"
      :min="SIDEBAR.min"
      :max="maxWidth"
      :label="t('nav.resize')"
      controls="p-sidebar-nav"
      testid="sidebar-resize"
      @resize="(px) => layout.setSidebarWidth(Math.min(px, maxWidth))"
      @reset="layout.resetSidebar()"
      @dragging="(v) => (dragging = v)"
    />
  </v-navigation-drawer>
</template>
