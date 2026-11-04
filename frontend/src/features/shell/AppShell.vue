<script setup lang="ts">
import { mdiChevronLeft, mdiChevronRight, mdiCogOutline, mdiFormatListBulleted, mdiHistory, mdiMicrophoneOutline, mdiPackageVariantClosed, mdiWrenchOutline } from "@mdi/js";
import { computed } from "vue";
import { useDisplay } from "vuetify";
import NotMigrated from "../../components/NotMigrated.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { type Mode, SECTION_STATUS, type SectionId, SECTIONS } from "../../domain/shell";
import ExpertWorkspace from "../expert/ExpertWorkspace.vue";
import SettingsPage from "../settings/SettingsPage.vue";
import NormalWorkspace from "../workspace/NormalWorkspace.vue";

const { shell } = useCore();
const state = useStore(shell.store);
const { t } = useI18n();
const display = useDisplay();
const rail = computed(() => state.value.navCollapsed || display.width.value < 1024);

const ICONS: Record<SectionId, string> = {
  transcribe: mdiMicrophoneOutline,
  results: mdiHistory,
  models: mdiPackageVariantClosed,
  queue: mdiFormatListBulleted,
  setup: mdiWrenchOutline,
  settings: mdiCogOutline,
};
// Where each un-migrated area is planned (docs/architecture/ui2/migration-next.md).
const STAGE: Partial<Record<SectionId, string>> = { results: "UI-3 S4", models: "UI-3 S4", queue: "UI-3 S3", setup: "UI-3 S4 / S5" };
const sections = computed(() => SECTIONS.map((id) => ({ id, title: t(`section.${id}`), status: SECTION_STATUS[id], icon: ICONS[id] })));
const currentTitle = computed(() => t(`section.${state.value.section}`));
</script>

<template>
  <v-layout class="fill-height">
    <v-app-bar density="compact" flat border="b">
      <v-app-bar-title>
        <span class="u-subheading">{{ t("app.title") }}</span>
        <v-chip size="x-small" label class="ml-2" color="warning" variant="outlined" data-testid="preview-chip">{{ t("app.preview") }}</v-chip>
      </v-app-bar-title>
      <v-btn-toggle
        :model-value="state.mode"
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
      <v-btn href="/" variant="text" size="small" class="mr-2" data-testid="open-legacy">{{ t("app.openLegacy") }}</v-btn>
    </v-app-bar>
    <v-navigation-drawer permanent :rail="rail" rail-width="56" width="232" border="e">
      <nav :aria-label="t('nav.label')">
        <v-list nav data-testid="sidebar">
          <v-list-item
            v-for="s in sections"
            :key="s.id"
            :active="s.id === state.section"
            :prepend-icon="s.icon"
            :title="s.title"
            :subtitle="rail ? undefined : s.status === 'partial' ? t('status.partial') : t('status.notMigrated')"
            :data-testid="`nav-${s.id}`"
            @click="shell.navigate(s.id)"
          />
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
          @click="shell.toggleNav()"
        >
          <template v-if="!rail">{{ t("nav.collapse") }}</template>
        </v-btn>
      </template>
    </v-navigation-drawer>
    <v-main>
      <v-alert type="warning" density="compact" class="ma-2" data-testid="preview-notice">{{ t("app.previewNotice") }}</v-alert>
      <template v-if="state.section === 'transcribe'">
        <ExpertWorkspace v-if="state.mode === 'expert'" />
        <NormalWorkspace v-else />
      </template>
      <SettingsPage v-else-if="state.section === 'settings'" />
      <section v-else class="u-page u-stack" :data-testid="`section-${state.section}`">
        <h1 class="u-heading">{{ currentTitle }}</h1>
        <NotMigrated :section="currentTitle" :stage="STAGE[state.section]" />
      </section>
    </v-main>
  </v-layout>
</template>
