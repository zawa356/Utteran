<script setup lang="ts">
import { mdiCogOutline, mdiFileDocumentOutline, mdiFolderOutline, mdiInformationOutline, mdiStarOutline, mdiTune } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { ACCENT_THEMES, type AccentTheme, type Appearance, APPEARANCES, type PreviewLanguage } from "../domain/demo";
import { SETTINGS_CATEGORIES, type SettingsCategory } from "../domain/layout";
import PresetManager from "../settings/PresetManager.vue";
import { ACCENT_SWATCH } from "../theme";

/**
 * Settings (iteration 3, owner memo #14). An information-design MOCK, not a working settings page:
 * only the colour theme, appearance and language change (in memory, never saved), and presets can
 * be managed in memory (iteration 4, #20/#22). Every other item is shown disabled and
 * labelled "later", because no API stores it yet (production GUI settings, B5 presets/policy and
 * the core config are three different sources of truth — see iteration-3.md §7).
 * Normal: a few app-wide items on one page. Expert: the same plus categories. Per-job settings
 * (language, model, diarization, formats) are NOT here: they belong to the Inspector.
 */
const { demo, layout, layoutState, state, t } = usePreview();
const expert = computed(() => layoutState.value.mode === "expert");
const category = computed(() => layoutState.value.settingsCategory);
const appearances = computed(() => APPEARANCES.map((v) => ({ value: v, title: t(`theme.${v}`) })));
const accents = computed(() => ACCENT_THEMES.map((v) => ({ value: v, title: t(`accent.${v}`) })));
const languages = [
  { value: "ja", title: "日本語" },
  { value: "en", title: "English" },
];
const ICONS: Record<SettingsCategory, string> = { general: mdiCogOutline, logs: mdiFileDocumentOutline, advanced: mdiTune, presets: mdiStarOutline };
const categories = computed(() => SETTINGS_CATEGORIES.map((id) => ({ id, icon: ICONS[id], title: t(`settings.cat.${id}`) })));
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="settings-heading" data-testid="section-settings" :data-variant="expert ? 'expert' : 'normal'">
    <h1 id="settings-heading" class="u-heading">{{ t("section.settings") }}</h1>
    <div class="p-feedback" role="note" data-testid="settings-status">
      <v-icon :icon="mdiInformationOutline" size="small" color="warning" aria-hidden="true" />
      <span>{{ t("settings.status") }}</span>
    </div>

    <div :class="expert ? 'p-settings' : ''">
      <v-list v-if="expert" nav :aria-label="t('settings.categories')" data-testid="settings-categories">
        <v-list-item
          v-for="c in categories"
          :key="c.id"
          :prepend-icon="c.icon"
          :title="c.title"
          :active="c.id === category"
          color="primary"
          :aria-current="c.id === category ? 'page' : undefined"
          :data-testid="`settings-cat-${c.id}`"
          @click="layout.setSettingsCategory(c.id)"
        />
      </v-list>

      <div class="p-settings__body" :class="{ 'p-settings__body--wide': expert && category === 'presets' }" :data-testid="`settings-body-${expert ? category : 'normal'}`">
        <!-- general (Normal shows only this) -->
        <template v-if="!expert || category === 'general'">
          <h2 v-if="expert" class="u-subheading">{{ t("settings.cat.general") }}</h2>
          <div class="u-stack" data-testid="settings-accent">
            <span id="settings-accent-label" class="text-body-medium font-weight-medium">{{ t("settings.accent") }}</span>
            <v-btn-toggle :model-value="state.accentTheme" mandatory divided variant="outlined" color="primary" aria-labelledby="settings-accent-label" @update:model-value="(v: AccentTheme) => demo.setAccentTheme(v)">
              <v-btn v-for="a in accents" :key="a.value" :value="a.value" :data-testid="`settings-accent-${a.value}`">
                <span class="p-swatch" :style="{ background: ACCENT_SWATCH[a.value] }" aria-hidden="true"></span>{{ a.title }}
              </v-btn>
            </v-btn-toggle>
          </div>
          <v-select :label="t('settings.theme')" :items="appearances" :model-value="state.appearance" data-testid="settings-theme" @update:model-value="(v) => demo.setAppearance(v as Appearance)" />
          <v-select :label="t('settings.language')" :items="languages" :model-value="state.language" data-testid="settings-language" @update:model-value="(v) => demo.setLanguage(v as PreviewLanguage)" />
          <div class="u-row" data-testid="settings-output">
            <v-text-field :label="t('settings.output')" :model-value="t('settings.outputValue')" readonly :prepend-inner-icon="mdiFolderOutline" hide-details class="flex-grow-1" />
            <v-btn variant="outlined" disabled>{{ t("settings.change") }}</v-btn>
          </div>
          <v-select v-if="expert" :label="t('settings.startMode')" :items="[t('mode.normal'), t('mode.expert')]" :model-value="t('mode.normal')" disabled :hint="t('settings.later')" persistent-hint />
        </template>

        <template v-else-if="category === 'logs'">
          <h2 class="u-subheading">{{ t("settings.cat.logs") }}</h2>
          <v-select :label="t('settings.logLevel')" :items="[t('settings.logLevel.standard'), t('settings.logLevel.detailed')]" :model-value="t('settings.logLevel.standard')" disabled :hint="t('settings.later')" persistent-hint />
          <v-select :label="t('settings.logKeep')" :items="[t('settings.logKeep.value')]" :model-value="t('settings.logKeep.value')" disabled :hint="t('settings.later')" persistent-hint />
          <div class="u-row">
            <v-btn variant="tonal" data-testid="settings-open-log" @click="layout.openDialog('systemLog')">{{ t("menu.tools.systemLog") }}</v-btn>
            <v-btn variant="tonal" data-testid="settings-open-diagnostics" @click="layout.openDialog('diagnostics')">{{ t("menu.tools.diagnostics") }}</v-btn>
          </div>
        </template>

        <template v-else-if="category === 'advanced'">
          <h2 class="u-subheading">{{ t("settings.cat.advanced") }}</h2>
          <v-text-field :label="t('settings.heavySlot')" :model-value="t('settings.heavySlot.value')" readonly :hint="t('settings.heavySlot.hint')" persistent-hint />
          <v-text-field :label="t('settings.temp')" :model-value="t('settings.temp.value')" disabled :hint="t('settings.later')" persistent-hint />
          <div class="u-row">
            <v-text-field :label="t('settings.token')" :model-value="t('settings.token.value')" readonly hide-details class="flex-grow-1" data-testid="settings-token" />
            <v-btn variant="outlined" disabled>{{ t("settings.change") }}</v-btn>
          </div>
          <v-switch :label="t('settings.resume')" color="primary" disabled hide-details />
        </template>

        <template v-else>
          <h2 class="u-subheading">{{ t("settings.cat.presets") }}</h2>
          <PresetManager />
        </template>
      </div>
    </div>
  </section>
</template>
