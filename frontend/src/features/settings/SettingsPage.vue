<script setup lang="ts">
import { computed } from "vue";
import { type DefaultProfile, type Language, PROFILES, type Theme } from "../../api/legacy";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { folderTooLong, isDirty } from "../../domain/settings";
import FolderField from "./FolderField.vue";
import LogCard from "./LogCard.vue";
import SetupCard from "./SetupCard.vue";
import TokenCard from "./TokenCard.vue";

/**
 * UI-3 S1: GUI settings through legacy GET/PATCH /api/settings (shared with the legacy GUI),
 * native folder choosers via HostBridge, token status/register/delete, log state, and a link to
 * the legacy setup wizard. Theme/language apply only after the server confirmed them.
 */
const { settings } = useCore();
const state = useStore(settings.store);
const { t } = useI18n();
const themes = computed(() =>
  (["system", "light", "dark"] as const).map((value) => ({ value, title: t(`settings.theme.${value}`) })),
);
const languages = computed(() => (["ja", "en"] as const).map((value) => ({ value, title: t(`settings.language.${value}`) })));
// null = "not specified" (a real value). Vuetify cannot use null as an item value, so map it.
const NONE = "__none__";
const profiles = computed(() => [
  { value: NONE, title: t("settings.profile.none") },
  ...PROFILES.map((value) => ({ value, title: t(`settings.profile.${value}`) })),
]);
const dirty = computed(() => isDirty(state.value));
const saving = computed(() => state.value.save.phase === "pending");
const busy = computed(() => saving.value || state.value.reconcile.phase === "pending");
const saveError = computed(() => (state.value.save.phase === "error" ? state.value.save.error : null));
const unknownOutcome = computed(() => saveError.value?.outcomeUnknown === true);
const canSave = computed(() => dirty.value && !busy.value && state.value.picking === null && !folderTooLong(state.value.draft));

function onProfile(value: string): void {
  settings.edit({ defaultProfile: value === NONE ? null : (value as DefaultProfile) });
}
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="settings-heading" data-testid="settings-page">
    <h1 id="settings-heading" class="u-heading">{{ t("settings.title") }}</h1>
    <p class="text-body-2" data-testid="settings-slice">{{ t("settings.slice") }}</p>
    <div v-if="state.load.phase === 'pending' && !state.draft" role="status" data-testid="settings-loading">
      <v-progress-linear indeterminate color="primary" />{{ t("settings.loading") }}
    </div>
    <ErrorAlert
      v-if="state.load.phase === 'error' && state.load.error && !state.draft"
      :error="state.load.error"
      :title="t('common.loadFailed')"
      testid="settings-load-error"
      @retry="settings.load()"
    />
    <form v-if="state.draft" class="u-stack" data-testid="settings-form" @submit.prevent="settings.save()">
      <v-card :title="t('settings.display')" class="u-card">
        <v-card-text class="u-grid">
          <v-select
            :label="t('settings.theme')"
            :items="themes"
            :model-value="state.draft.theme"
            :disabled="busy"
            data-testid="settings-theme"
            @update:model-value="(v: Theme) => settings.edit({ theme: v })"
          />
          <v-select
            :label="t('settings.language')"
            :items="languages"
            :model-value="state.draft.language"
            :disabled="busy"
            data-testid="settings-language"
            @update:model-value="(v: Language) => settings.edit({ language: v })"
          />
        </v-card-text>
      </v-card>
      <v-card :title="t('settings.defaults')" :subtitle="t('settings.defaultsHint')" class="u-card">
        <v-card-text class="u-stack">
          <v-select
            :label="t('settings.profile')"
            :items="profiles"
            :model-value="state.draft.defaultProfile ?? NONE"
            :disabled="busy"
            :hint="t('settings.profileHint')"
            persistent-hint
            class="u-w-md"
            data-testid="settings-profile"
            @update:model-value="onProfile"
          />
          <FolderField field="defaultInputDir" :label="t('settings.inputDir')" testid="settings-input-dir" />
          <FolderField field="defaultOutputDir" :label="t('settings.outputDir')" testid="settings-output-dir" />
          <p class="text-body-2">{{ t("settings.folderHint") }}</p>
          <v-alert v-if="state.saved?.portable" type="info" data-testid="settings-portable">{{ t("settings.portable") }}</v-alert>
        </v-card-text>
      </v-card>
      <div class="u-row">
        <v-btn type="submit" color="primary" :loading="saving" :disabled="!canSave" data-testid="settings-save">
          {{ t("settings.save") }}
        </v-btn>
        <v-btn variant="text" :disabled="!dirty || busy" data-testid="settings-revert" @click="settings.revert()">
          {{ t("settings.revert") }}
        </v-btn>
        <span role="status" aria-live="polite" class="text-body-2" data-testid="settings-status">
          <template v-if="saving">{{ t("settings.saving") }}</template>
          <template v-else-if="state.reconcile.phase === 'pending'">{{ t("settings.reconciling") }}</template>
          <template v-else-if="state.reconciled === 'applied' && !dirty">{{ t("settings.reconciledApplied") }}</template>
          <template v-else-if="state.reconciled === 'not_applied' && dirty">{{ t("settings.reconciledNotApplied") }}</template>
          <template v-else-if="state.save.phase === 'success' && !dirty">{{ t("settings.saved") }}</template>
          <template v-else-if="dirty">{{ t("settings.unsaved") }}</template>
        </span>
      </div>
      <v-alert v-if="unknownOutcome" type="warning" role="alert" :title="t('settings.saveUnknown')" data-testid="settings-save-unknown">
        {{ t("settings.saveUnknownBody") }}
        <template #append>
          <v-btn variant="text" :loading="state.reconcile.phase === 'pending'" data-testid="settings-reconcile" @click="settings.reconcile()">
            {{ t("settings.reconcile") }}
          </v-btn>
        </template>
      </v-alert>
      <ErrorAlert
        v-else-if="saveError"
        :error="saveError"
        :title="t('settings.saveFailed')"
        testid="settings-save-error"
        @retry="settings.save()"
      />
      <ErrorAlert
        v-if="state.reconcile.phase === 'error' && state.reconcile.error"
        :error="state.reconcile.error"
        :title="t('common.loadFailed')"
        testid="settings-reconcile-error"
        @retry="settings.reconcile()"
      />
    </form>
    <TokenCard />
    <LogCard />
    <SetupCard />
  </section>
</template>
