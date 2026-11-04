<script setup lang="ts">
import { computed } from "vue";
import type { Language, Theme } from "../../api/legacy";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { isDirty } from "../../domain/settings";

/** S1 slice: theme + language through legacy GET/PATCH /api/settings. */
const { settings } = useCore();
const state = useStore(settings.store);
const { t } = useI18n();
const themes = computed(() =>
  (["system", "light", "dark"] as const).map((value) => ({ value, title: t(`settings.theme.${value}`) })),
);
const languages = computed(() => (["ja", "en"] as const).map((value) => ({ value, title: t(`settings.language.${value}`) })));
const dirty = computed(() => isDirty(state.value));
const saving = computed(() => state.value.save.phase === "pending");
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="settings-heading" data-testid="settings-page">
    <h1 id="settings-heading" class="u-heading">{{ t("settings.title") }}</h1>
    <v-alert type="info">{{ t("settings.slice") }}</v-alert>
    <div v-if="state.load.phase === 'pending' && !state.draft" role="status" data-testid="settings-loading">
      <v-progress-linear indeterminate color="primary" />{{ t("settings.loading") }}
    </div>
    <ErrorAlert
      v-if="state.load.phase === 'error' && state.load.error"
      :error="state.load.error"
      :title="t('common.loadFailed')"
      testid="settings-load-error"
      @retry="settings.load()"
    />
    <form v-if="state.draft" class="u-stack u-w-md" @submit.prevent="settings.save()">
      <v-select
        :label="t('settings.theme')"
        :items="themes"
        :model-value="state.draft.theme"
        :disabled="saving"
        data-testid="settings-theme"
        @update:model-value="(v: Theme) => settings.edit({ theme: v })"
      />
      <v-select
        :label="t('settings.language')"
        :items="languages"
        :model-value="state.draft.language"
        :disabled="saving"
        data-testid="settings-language"
        @update:model-value="(v: Language) => settings.edit({ language: v })"
      />
      <div class="u-row">
        <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || saving" data-testid="settings-save">
          {{ t("settings.save") }}
        </v-btn>
        <v-btn variant="text" :disabled="!dirty || saving" data-testid="settings-revert" @click="settings.revert()">
          {{ t("settings.revert") }}
        </v-btn>
        <span role="status" aria-live="polite" class="text-body-2" data-testid="settings-status">
          <template v-if="saving">{{ t("settings.saving") }}</template>
          <template v-else-if="state.save.phase === 'success' && !dirty">{{ t("settings.saved") }}</template>
          <template v-else-if="dirty">{{ t("settings.unsaved") }}</template>
        </span>
      </div>
      <ErrorAlert
        v-if="state.save.phase === 'error' && state.save.error"
        :error="state.save.error"
        :title="t('settings.saveFailed')"
        testid="settings-save-error"
        @retry="settings.save()"
      />
    </form>
    <p v-if="state.saved" class="text-body-2" data-testid="settings-token">
      {{ t("settings.token") }}: {{ state.saved.tokenConfigured ? t("settings.tokenConfigured") : t("settings.tokenMissing") }}
    </p>
  </section>
</template>
