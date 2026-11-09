<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { isUnconfirmed, tokenInputValid, tokenView } from "../../domain/token";
import { errorMessage, type MessageKey } from "../../i18n";

/**
 * Hugging Face token: current presence, register, delete. The typed value lives only in this
 * component's local ref and is cleared as soon as a request is sent (success or not), so a
 * resend is always a fresh, explicit entry. The status line is the server's *current presence*;
 * whether the last PUT/DELETE took effect is a separate note, because a GET cannot prove it.
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
// A definitive rejection (e.g. 422/401). 5xx and lost answers are shown as "unconfirmed" instead.
const actionError = computed(() => {
  const error = state.value.action.phase === "error" ? state.value.action.error : null;
  return error && isUnconfirmed(error) === null ? error : null;
});
const unconfirmedTitle = computed(() => {
  const u = state.value.unconfirmed;
  return u ? t(`token.unconfirmed.${u.kind}.${u.reason}` as MessageKey) : "";
});
const unconfirmedBody = computed(() => {
  const u = state.value.unconfirmed;
  if (!u) return "";
  if (!state.value.statusCurrent) return t("token.unconfirmed.readNow");
  return u.kind === "set" ? t("token.unconfirmed.setCurrent") : t("token.unconfirmed.clearCurrent");
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
// Only for a 2xx answer to the action itself; a later GET resets it (never re-shown by a re-check).
const doneText = computed(() => {
  if (state.value.action.phase !== "success") return "";
  return state.value.actionKind === "clear" ? t("token.deleted") : t("token.registered");
});

onMounted(() => {
  if (state.value.load.phase === "idle") void token.load();
});

async function register(): Promise<void> {
  const entered = value.value;
  if (!tokenInputValid(entered) || pending.value) return;
  value.value = ""; // cleared before the request: the field never keeps a value that was sent
  await token.set(entered);
}
async function remove(): Promise<void> {
  confirming.value = false;
  await token.clear();
}
</script>

<template>
  <v-card :title="t('token.title')" class="u-card" data-testid="token-card">
    <v-card-text class="u-stack">
      <p role="status" aria-live="polite" class="text-body-1" :data-view="view" data-testid="token-status">
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
      <v-alert
        v-if="state.unconfirmed && !pending"
        type="warning"
        role="alert"
        :title="unconfirmedTitle"
        :data-reason="state.unconfirmed.reason"
        data-testid="token-unconfirmed"
      >
        <p data-testid="token-unconfirmed-body">{{ unconfirmedBody }}</p>
        <p v-if="state.load.phase === 'error' && !state.statusCurrent" class="mt-1" data-testid="token-recheck-failed">{{ t("token.recheckFailed") }}</p>
        <div class="u-row mt-2">
          <v-btn variant="outlined" size="small" :loading="state.load.phase === 'pending'" data-testid="token-recheck" @click="token.load()">
            {{ t("token.recheck") }}
          </v-btn>
          <v-btn v-if="state.statusCurrent" variant="text" size="small" data-testid="token-acknowledge" @click="token.acknowledge()">
            {{ t("token.acknowledge") }}
          </v-btn>
        </div>
      </v-alert>
      <v-alert v-if="sessionOnly" type="info" data-testid="token-session-only">{{ t("token.sessionOnly") }}</v-alert>
      <v-alert v-if="actionError" type="error" role="alert" data-testid="token-action-error">
        {{ t("token.actionFailed") }} {{ errorMessage(language, actionError) }}
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
