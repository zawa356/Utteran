<script setup lang="ts">
import { computed } from "vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { errorMessage } from "../../i18n";

/** Log location and raw-log state from GET /api/settings (read-only); open = server log_dir only. */
const { logs, settings } = useCore();
const state = useStore(logs.store);
const settingsState = useStore(settings.store);
const { language, t } = useI18n();
const log = computed(() => settingsState.value.saved?.log ?? null);
const errorText = computed(() => {
  const error = state.value.open.error;
  if (state.value.open.phase !== "error" || !error) return "";
  if (error.status === 400) return t("log.openMissing");
  if (error.status === 503) return t("log.openUnavailable");
  return `${t("log.openFailed")} ${errorMessage(language.value, error)}`;
});
</script>

<template>
  <v-card :title="t('log.title')" class="u-card" data-testid="log-card">
    <v-card-text v-if="log" class="u-stack">
      <div>
        <div class="text-body-2">{{ t("log.dir") }}</div>
        <div class="u-path" data-testid="log-dir">{{ log.dir ?? t("log.dirUnknown") }}</div>
      </div>
      <v-alert v-if="log.fallback" type="info" data-testid="log-fallback">{{ t("log.fallback") }}</v-alert>
      <v-alert v-if="log.rawSubprocessLogs" type="warning" :title="t('log.raw')" data-testid="log-raw-warning">{{ t("log.rawSource") }}</v-alert>
      <div class="u-row">
        <v-btn
          variant="outlined"
          :loading="state.open.phase === 'pending'"
          :disabled="log.dir === null || state.open.phase === 'pending'"
          data-testid="log-open"
          @click="logs.open(log.dir)"
        >
          {{ t("log.open") }}
        </v-btn>
        <span role="status" aria-live="polite" class="text-body-2" data-testid="log-open-status">
          <template v-if="state.open.phase === 'success'">{{ t("log.opened") }}</template>
        </span>
      </div>
      <v-alert v-if="errorText" type="error" role="alert" data-testid="log-open-error">{{ errorText }}</v-alert>
    </v-card-text>
  </v-card>
</template>
