<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { tokenInputValid, tokenView } from "../../domain/token";
import { errorMessage } from "../../i18n";

/**
 * Hugging Face token: status, register, delete. The typed value lives only in this component's
 * local ref (never a store, storage, URL, log or error) and is cleared after the server confirms.
 */
const { settings, token } = useCore();
const state = useStore(token.store);
const settingsState = useStore(settings.store);
const { language, t } = useI18n();
const value = ref("");
const confirming = ref(false);
const view = computed(() => tokenView(state.value));
const pending = computed(() => state.value.action.phase === "pending");
const sessionOnly = computed(() => state.value.status?.sessionOnly === true || settingsState.value.saved?.token.sessionOnly === true);
const actionError = computed(() => (state.value.action.phase === "error" && !state.value.outcomeUnknown ? state.value.action.error : null));
const actionErrorText = computed(() => {
  const error = actionError.value;
  if (!error) return "";
  // Fixed text for the keyring 503; the server's detail string is never shown.
  if (error.status === 503) return t("token.storeUnavailableError");
  return `${t("token.actionFailed")} ${errorMessage(language.value, error)}`;
});
const statusText = computed(() => {
  switch (view.value) {
    case "loading":
      return t("token.loading");
    case "configured":
      return sessionOnly.value ? t("token.configuredSession") : t("token.configured");
    case "missing":
      return t("token.missing");
    case "unavailable":
      return t("token.unavailable");
    case "unknown":
      return t("token.unknown");
    default:
      return "";
  }
});
const doneText = computed(() => {
  if (state.value.action.phase !== "success") return "";
  return state.value.actionKind === "clear" ? t("token.deleted") : t("token.registered");
});

onMounted(() => {
  if (state.value.load.phase === "idle") void token.load();
});

async function register(): Promise<void> {
  if (await token.set(value.value)) value.value = "";
}
async function remove(): Promise<void> {
  confirming.value = false;
  await token.clear();
}
</script>

<template>
  <v-card :title="t('token.title')" class="u-card" data-testid="token-card">
    <v-card-text class="u-stack">
      <p v-show="view !== 'unknown'" role="status" aria-live="polite" class="text-body-1" :data-view="view" data-testid="token-status">
        <span v-if="pending">{{ state.actionKind === "clear" ? t("token.deleting") : t("token.registering") }}</span>
        <span v-else>{{ statusText }}</span>
      </p>
      <ErrorAlert
        v-if="view === 'load_error' && state.load.error"
        :error="state.load.error"
        :title="t('token.loadFailed')"
        testid="token-load-error"
        @retry="token.load()"
      />
      <v-alert v-if="view === 'unknown'" type="warning" role="alert" data-testid="token-unknown">
        {{ t("token.unknown") }}
        <template #append>
          <v-btn variant="text" :loading="state.load.phase === 'pending'" data-testid="token-recheck" @click="token.load()">{{ t("token.recheck") }}</v-btn>
        </template>
      </v-alert>
      <v-alert v-if="sessionOnly" type="info" data-testid="token-session-only">{{ t("token.sessionOnly") }}</v-alert>
      <v-alert v-if="actionError" type="error" role="alert" data-testid="token-action-error">
        {{ actionErrorText }}
        <template #append>
          <v-btn variant="text" :loading="state.load.phase === 'pending'" data-testid="token-action-recheck" @click="token.load()">{{ t("token.recheck") }}</v-btn>
        </template>
      </v-alert>
      <form class="u-folder" autocomplete="off" @submit.prevent="register">
        <v-text-field
          v-model="value"
          type="password"
          :label="t('token.input')"
          :hint="t('token.inputHint')"
          persistent-hint
          autocomplete="new-password"
          spellcheck="false"
          autocapitalize="off"
          :disabled="pending"
          class="u-folder__input"
          data-testid="token-input"
        />
        <div class="u-row u-folder__actions">
          <v-btn type="submit" color="primary" :loading="pending && state.actionKind === 'set'" :disabled="pending || !tokenInputValid(value)" data-testid="token-register">
            {{ sessionOnly ? t("token.registerSession") : t("token.register") }}
          </v-btn>
          <v-btn
            variant="outlined"
            color="error"
            :loading="pending && state.actionKind === 'clear'"
            :disabled="pending || view === 'missing' || view === 'loading'"
            data-testid="token-delete"
            @click="confirming = true"
          >
            {{ t("token.delete") }}
          </v-btn>
          <span role="status" aria-live="polite" class="text-body-2" data-testid="token-done">{{ doneText }}</span>
        </div>
      </form>
    </v-card-text>
    <v-dialog v-model="confirming" max-width="420" data-testid="token-confirm">
      <v-card :title="t('token.confirmTitle')" data-testid="token-confirm-card">
        <v-card-text>{{ t("token.confirmBody") }}</v-card-text>
        <v-card-actions>
          <v-btn color="error" variant="flat" data-testid="token-confirm-delete" @click="remove">{{ t("token.confirmDelete") }}</v-btn>
          <v-btn variant="text" data-testid="token-confirm-cancel" @click="confirming = false">{{ t("common.cancel") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>
