<script setup lang="ts">
import { computed } from "vue";
import PrototypeBanner from "./components/PrototypeBanner.vue";
import { usePreview } from "./composables";
import type { Mode } from "./domain/layout";
import ExpertLayout from "./expert/ExpertLayout.vue";
import { isPreviewKey, type PreviewKey } from "./i18n";
import NormalHome from "./normal/NormalHome.vue";
import LaterSection from "./sections/LaterSection.vue";
import QueueSection from "./sections/QueueSection.vue";
import ResultsSection from "./sections/ResultsSection.vue";
import SettingsSection from "./sections/SettingsSection.vue";
import SetupSection from "./sections/SetupSection.vue";
import ExpertSidebar from "./shell/ExpertSidebar.vue";
import NormalTopNav from "./shell/NormalTopNav.vue";
import AppDialogs from "./shell/AppDialogs.vue";
import AppMenu from "./shell/AppMenu.vue";
import ReviewStrip from "./shell/ReviewStrip.vue";

/**
 * The mode switches the whole information architecture (iteration 2): Normal = app bar with three
 * destinations, no sidebar, one file; Expert = resizable management sidebar with six destinations.
 * Iteration 3 adds the shared application menu (File / View / Tools / Help) at the left of the app
 * bar and keeps one short prototype line under it. The demo data is never changed by the switch.
 */
const { layout, layoutState, state, t } = usePreview();
const mode = computed(() => layoutState.value.mode);
const section = computed(() => layoutState.value.section);

const noticeText = computed(() => {
  const n = state.value.notice;
  if (!n || !isPreviewKey(n.key)) return "";
  const params = { ...n.params };
  if (typeof params.scenario === "string") params.scenario = t(`scenario.${params.scenario}` as PreviewKey);
  return t(n.key, params);
});
</script>

<template>
  <v-layout class="fill-height" :data-mode="mode">
    <v-app-bar density="compact" height="52" flat border="b" data-testid="app-bar">
      <div class="p-brand">Utteran</div>
      <AppMenu />
      <template v-if="mode === 'normal'">
        <span class="p-appbar-sep" aria-hidden="true"></span>
        <NormalTopNav />
      </template>
      <v-spacer />
      <v-btn-toggle
        :model-value="mode"
        mandatory
        density="compact"
        variant="outlined"
        divided
        color="primary"
        :aria-label="t('mode.label')"
        class="mr-2"
        data-testid="mode-toggle"
        @update:model-value="(m: Mode) => layout.setMode(m)"
      >
        <v-btn value="normal" data-testid="mode-normal">{{ t("mode.normal") }}</v-btn>
        <v-btn value="expert" data-testid="mode-expert">{{ t("mode.expert") }}</v-btn>
      </v-btn-toggle>
    </v-app-bar>
    <ExpertSidebar v-if="mode === 'expert'" />
    <v-main>
      <div class="p-main">
        <PrototypeBanner />
        <div class="p-content" :class="{ 'p-content--fill': mode === 'expert' && section === 'transcribe' }" data-testid="content">
          <template v-if="section === 'transcribe'">
            <ExpertLayout v-if="mode === 'expert'" />
            <NormalHome v-else />
          </template>
          <SetupSection v-else-if="section === 'setup'" />
          <SettingsSection v-else-if="section === 'settings'" />
          <QueueSection v-else-if="section === 'queue'" />
          <ResultsSection v-else-if="section === 'results'" />
          <LaterSection v-else />
        </div>
      </div>
    </v-main>
    <ReviewStrip />
    <AppDialogs />
    <v-snackbar :key="state.notice?.seq ?? 0" :model-value="noticeText !== ''" timeout="2500" location="top end" color="secondary" role="status" data-testid="notice">
      {{ noticeText }}
    </v-snackbar>
  </v-layout>
</template>
