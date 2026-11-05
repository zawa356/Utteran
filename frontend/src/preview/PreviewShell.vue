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
import ReviewStrip from "./shell/ReviewStrip.vue";

/**
 * Iteration 2: the mode switches the whole information architecture.
 * Normal = app bar with three destinations, no sidebar, one file. Expert = resizable management
 * sidebar with six destinations. The demo data is shared and never changed by the switch.
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
    <v-app-bar density="compact" flat border="b" data-testid="app-bar">
      <div class="p-brand">
        <span class="u-subheading">Utteran</span>
        <v-chip size="x-small" label color="warning" variant="outlined" data-testid="prototype-chip">{{ t("app.prototype") }}</v-chip>
      </div>
      <NormalTopNav v-if="mode === 'normal'" />
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
        <v-btn value="normal" size="small" data-testid="mode-normal">{{ t("mode.normal") }}</v-btn>
        <v-btn value="expert" size="small" data-testid="mode-expert">{{ t("mode.expert") }}</v-btn>
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
    <v-snackbar :key="state.notice?.seq ?? 0" :model-value="noticeText !== ''" timeout="2500" location="top end" color="secondary" role="status" data-testid="notice">
      {{ noticeText }}
    </v-snackbar>
  </v-layout>
</template>
