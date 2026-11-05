<script setup lang="ts">
import { mdiChevronLeft, mdiChevronRight, mdiCogOutline, mdiFormatListBulleted, mdiHistory, mdiMicrophoneOutline, mdiPackageVariantClosed, mdiWrenchOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { useDisplay } from "vuetify";
import { type Mode, type SectionId, SECTIONS } from "../domain/shell";
import PrototypeBanner from "./components/PrototypeBanner.vue";
import { usePreview } from "./composables";
import { isActive } from "./domain/demo";
import ExpertLayout from "./expert/ExpertLayout.vue";
import { isPreviewKey, type PreviewKey } from "./i18n";
import NormalFlow from "./normal/NormalFlow.vue";
import PreviewToolbar from "./PreviewToolbar.vue";
import LaterSection from "./sections/LaterSection.vue";
import QueueSection from "./sections/QueueSection.vue";
import ResultsSection from "./sections/ResultsSection.vue";
import SettingsSection from "./sections/SettingsSection.vue";

/** Same frame as features/shell/AppShell.vue (app bar, permanent sidebar, rail < 1024 px). */
const { shell, shellState, state, t } = usePreview();
const display = useDisplay();
// Adjustment found in the 1280 px review shots: in Expert mode the three panes need the sidebar
// width, so the sidebar becomes a rail below 1440 px (still expandable by the toggle at ≥1024 px).
const expandedByUser = ref(false);
const autoRail = computed(() => display.width.value < 1024 || (shellState.value.mode === "expert" && shellState.value.section === "transcribe" && display.width.value < 1440));
const rail = computed(() => (autoRail.value && !expandedByUser.value) || shellState.value.navCollapsed || display.width.value < 1024);
function toggleNav() {
  if (autoRail.value) expandedByUser.value = !expandedByUser.value;
  else shell.toggleNav();
}
const ICONS: Record<SectionId, string> = {
  transcribe: mdiMicrophoneOutline,
  results: mdiHistory,
  models: mdiPackageVariantClosed,
  queue: mdiFormatListBulleted,
  setup: mdiWrenchOutline,
  settings: mdiCogOutline,
};
const activeJobs = computed(() => state.value.jobs.filter(isActive).length);
const sections = computed(() => SECTIONS.map((id) => ({ id, title: t(`section.${id}`), icon: ICONS[id], subtitle: t(`nav.sub.${id}`) })));

const noticeText = computed(() => {
  const n = state.value.notice;
  if (!n || !isPreviewKey(n.key)) return "";
  const params = { ...n.params };
  if (typeof params.scenario === "string") params.scenario = t(`scenario.${params.scenario}` as PreviewKey);
  return t(n.key, params);
});
</script>

<template>
  <v-layout class="fill-height">
    <v-app-bar density="compact" flat border="b">
      <v-app-bar-title>
        <span class="u-subheading">Utteran</span>
        <v-chip size="x-small" label class="ml-2" color="warning" variant="outlined" data-testid="prototype-chip">{{ t("app.prototype") }}</v-chip>
      </v-app-bar-title>
      <v-btn-toggle
        :model-value="shellState.mode"
        mandatory
        density="compact"
        variant="outlined"
        divided
        :aria-label="t('mode.label')"
        class="mr-2"
        data-testid="mode-toggle"
        @update:model-value="(m: Mode) => shell.setMode(m)"
      >
        <v-btn value="normal" size="small" data-testid="mode-normal">{{ t("mode.normal") }}</v-btn>
        <v-btn value="expert" size="small" data-testid="mode-expert">{{ t("mode.expert") }}</v-btn>
      </v-btn-toggle>
    </v-app-bar>
    <v-navigation-drawer permanent :rail="rail" rail-width="56" width="232" border="e">
      <nav :aria-label="t('nav.label')">
        <v-list nav data-testid="sidebar">
          <v-list-item
            v-for="s in sections"
            :key="s.id"
            :active="s.id === shellState.section"
            :prepend-icon="s.icon"
            :title="s.title"
            :subtitle="rail ? undefined : s.subtitle"
            :data-testid="`nav-${s.id}`"
            @click="shell.navigate(s.id)"
          >
            <template v-if="s.id === 'queue' && activeJobs > 0 && !rail" #append>
              <v-chip size="x-small" label color="primary" variant="tonal" data-testid="nav-queue-count">{{ activeJobs }}</v-chip>
            </template>
          </v-list-item>
        </v-list>
      </nav>
      <template #append>
        <v-btn
          block
          variant="text"
          size="small"
          :icon="rail ? mdiChevronRight : undefined"
          :prepend-icon="rail ? undefined : mdiChevronLeft"
          :aria-label="rail ? t('nav.expand') : t('nav.collapse')"
          :disabled="display.width.value < 1024"
          data-testid="nav-toggle"
          @click="toggleNav()"
        >
          <template v-if="!rail">{{ t("nav.collapse") }}</template>
        </v-btn>
      </template>
    </v-navigation-drawer>
    <v-main>
      <PrototypeBanner />
      <PreviewToolbar />
      <template v-if="shellState.section === 'transcribe'">
        <ExpertLayout v-if="shellState.mode === 'expert'" />
        <NormalFlow v-else />
      </template>
      <ResultsSection v-else-if="shellState.section === 'results'" />
      <QueueSection v-else-if="shellState.section === 'queue'" />
      <SettingsSection v-else-if="shellState.section === 'settings'" />
      <LaterSection v-else :section="shellState.section === 'models' ? 'models' : 'setup'" />
    </v-main>
    <v-snackbar :key="state.notice?.seq ?? 0" :model-value="noticeText !== ''" timeout="2500" location="bottom end" color="secondary" role="status" data-testid="notice">
      {{ noticeText }}
    </v-snackbar>
  </v-layout>
</template>
