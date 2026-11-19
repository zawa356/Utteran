<script setup lang="ts">
import { computed } from "vue";
import LegacyLink from "../../components/LegacyLink.vue";
import NotMigrated from "../../components/NotMigrated.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { type Mode, previewNoticeKey, type SectionId } from "../../domain/shell";
import ExpertWorkspace from "../expert/ExpertWorkspace.vue";
import SettingsPage from "../settings/SettingsPage.vue";
import NormalWorkspace from "../workspace/NormalWorkspace.vue";
import QueuePage from "../queue/QueuePage.vue";
import ExpertSidebar from "./ExpertSidebar.vue";
import NormalTopNav from "./NormalTopNav.vue";

/**
 * The one application shell (bundle D1, Design Freeze): Normal = three destinations in the app bar
 * and no sidebar; Expert = the six-destination management sidebar. The mode changes only the
 * navigation and the page layout; every page reads the same AppCore controllers, so a switch
 * never submits, cancels, deletes or saves anything.
 */
const { shell, apiMode } = useCore();
// Server-rendered API mode only (S5-A): the legacy text stays as it is for `--next-ui`.
const notice = previewNoticeKey(apiMode);
const state = useStore(shell.store);
const { t } = useI18n();

// Where each un-migrated area is planned (queue: bundle D2 — v1 has the D2-A page, legacy keeps this notice; results: D3).
const STAGE: Partial<Record<SectionId, string>> = { results: "UI-3 D3", models: "UI-3 S4", queue: "UI-3 D2", setup: "UI-3 S4 / S5" };
const currentTitle = computed(() => t(`section.${state.value.section}`));
</script>

<template>
  <v-layout class="fill-height" :data-mode="state.mode">
    <v-app-bar density="compact" flat border="b" data-testid="app-bar">
      <div class="u-brand">
        <span class="u-subheading">{{ t("app.title") }}</span>
        <v-chip size="x-small" label class="ml-2" color="warning" variant="outlined" data-testid="preview-chip">{{ t("app.preview") }}</v-chip>
      </div>
      <template v-if="state.mode === 'normal'">
        <span class="u-appbar-sep" aria-hidden="true"></span>
        <NormalTopNav />
      </template>
      <v-spacer />
      <v-btn-toggle
        :model-value="state.mode"
        mandatory
        density="compact"
        variant="outlined"
        divided
        :aria-label="t('mode.label')"
        class="mr-2 u-no-shrink"
        data-testid="mode-toggle"
        @update:model-value="(m: Mode) => shell.setMode(m)"
      >
        <v-btn value="normal" size="small" data-testid="mode-normal">{{ t("mode.normal") }}</v-btn>
        <v-btn value="expert" size="small" data-testid="mode-expert">{{ t("mode.expert") }}</v-btn>
      </v-btn-toggle>
      <LegacyLink :label="t('app.openLegacy')" size="small" testid="open-legacy" class="mr-2 u-no-shrink" />
    </v-app-bar>
    <ExpertSidebar v-if="state.mode === 'expert'" />
    <v-main data-testid="content" :data-section="state.section">
      <v-alert type="warning" density="compact" class="ma-2" data-testid="preview-notice" :data-notice="notice">{{ t(notice) }}</v-alert>
      <template v-if="state.section === 'transcribe'">
        <ExpertWorkspace v-if="state.mode === 'expert'" />
        <NormalWorkspace v-else />
      </template>
      <SettingsPage v-else-if="state.section === 'settings'" />
      <!-- D2-A: the queue page exists only in the v1 shape (`--next-ui-v1`); legacy keeps the notice. -->
      <QueuePage v-else-if="state.section === 'queue' && apiMode === 'v1-dev'" />
      <section v-else class="u-page u-stack" :data-testid="`section-${state.section}`">
        <h1 class="u-heading">{{ currentTitle }}</h1>
        <NotMigrated :section="currentTitle" :stage="STAGE[state.section]" />
      </section>
    </v-main>
  </v-layout>
</template>
