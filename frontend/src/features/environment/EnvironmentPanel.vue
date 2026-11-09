<script setup lang="ts">
import { mdiRefresh } from "@mdi/js";
import { computed, onUnmounted, ref, watch } from "vue";
import { ENVIRONMENT_TIMEOUT_MS, PROFILES } from "../../api/legacy";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { environmentView, isCurrentResult } from "../../domain/environment";
import { noticeCount, profileStatus, selectionNote } from "../../domain/environmentSummary";
import type { MessageKey } from "../../i18n";
import EnvironmentDetails from "./EnvironmentDetails.vue";
import EnvironmentSummary from "./EnvironmentSummary.vue";

/**
 * S2: legacy GET /api/environment, read-only. Normal shows the four-item summary with a
 * collapsed detail; Expert (`detail`) shows the categories open. Both read the one shared
 * `AppCore.environment` (no fetch of their own). Never claims "ready" (G-01).
 */
const props = defineProps<{ detail?: boolean }>();
const { environment, settings } = useCore();
const state = useStore(environment.store);
const settingsState = useStore(settings.store);
const { language, t } = useI18n();

const DEFAULT = "__default__";
const savedDefault = computed(() => settingsState.value.saved?.defaultProfile ?? null);
const profileItems = computed(() => [
  { title: t("env.profileDefault", { profile: savedDefault.value ?? t("env.defaultUnset") }), value: DEFAULT, props: { disabled: false } },
  ...PROFILES.map((name) => {
    const row = state.value.data?.profiles.find((p) => p.name === name);
    const status = row ? t(`env.status.${profileStatus(row)}` as MessageKey) : t("env.status.notLoaded");
    // A profile the last answer reported as not built is shown but not selectable.
    return { title: `${name} — ${status}`, value: name, props: { disabled: row?.exists === false } };
  }),
]);
const selected = computed(() => (state.value.followsDefault ? DEFAULT : (state.value.requestedProfile ?? DEFAULT)));
const view = computed(() => environmentView(state.value));
const pending = computed(() => view.value === "pending");
const current = computed(() => isCurrentResult(state.value));
const note = computed(() => selectionNote(state.value));
const notices = computed(() => {
  const data = state.value.data;
  if (!data || !current.value) return 0;
  const n = noticeCount(data);
  return n.issues + n.rebuild + n.guidance;
});

function onSelect(value: string | null) {
  if (value === null || value === DEFAULT) void environment.selectDefault();
  else void environment.select(value);
}

// Elapsed time on the client clock while waiting (the API reports no stage or progress).
const clock = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
watch(
  pending,
  (isPending) => {
    if (timer) clearInterval(timer);
    timer = null;
    clock.value = Date.now();
    if (isPending) timer = setInterval(() => (clock.value = Date.now()), 1000);
  },
  { immediate: true },
);
onUnmounted(() => timer && clearInterval(timer));
const elapsed = computed(() => Math.max(0, Math.floor((clock.value - (state.value.startedAt ?? clock.value)) / 1000)));
const limitSeconds = ENVIRONMENT_TIMEOUT_MS / 1000;

const previous = computed(() => {
  const s = state.value;
  if (!s.data || !s.dataFor || current.value) return null;
  const target = s.dataFor.followsDefault ? t("env.targetDefault") : (s.dataFor.profile ?? t("env.targetServer"));
  const time = new Date(s.dataFor.receivedAt).toLocaleTimeString(language.value === "ja" ? "ja-JP" : "en-US");
  return t("env.previousBody", { target, time });
});
const noteText = computed(() => {
  const n = note.value;
  if (!n) return null;
  if (n.kind === "fallback") return t("env.note.fallback", n);
  if (n.kind === "requested_unavailable") return t("env.note.requestedUnavailable", n);
  if (n.kind === "server_choice") return t("env.note.serverChoice", n);
  if (n.kind === "choose") return t("env.note.choose");
  return t("env.note.noneUsable");
});
const noteUnchanged = computed(() => state.value.followsDefault && (note.value?.kind === "fallback" || note.value?.kind === "requested_unavailable"));
</script>

<template>
  <section class="u-stack" aria-labelledby="env-heading" data-testid="environment-panel" :data-view="view">
    <div class="u-row">
      <h2 id="env-heading" class="u-subheading">{{ t("env.title") }}</h2>
      <v-chip size="small" label color="warning" variant="outlined" data-testid="env-readiness">{{ t("env.unverified") }}</v-chip>
    </div>
    <div class="u-row">
      <v-select
        :label="t('env.profile')"
        :items="profileItems"
        :model-value="selected"
        class="u-w-md"
        hide-details
        data-testid="env-profile"
        @update:model-value="onSelect"
      />
      <v-btn variant="tonal" :prepend-icon="mdiRefresh" :disabled="pending" data-testid="env-redetect" @click="environment.redetect()">
        {{ t("env.redetect") }}
      </v-btn>
      <v-btn v-if="pending" variant="text" data-testid="env-cancel" @click="environment.cancel()">{{ t("env.cancel") }}</v-btn>
    </div>

    <div v-if="pending" role="status" aria-live="polite" class="u-stack" data-testid="env-loading">
      <v-progress-linear indeterminate color="primary" />
      <div class="text-body-2">{{ t("env.loading") }}</div>
      <div class="text-caption">{{ t("env.loadingSlow") }} {{ t("env.limit", { seconds: limitSeconds }) }}</div>
      <div class="text-caption" data-testid="env-elapsed">{{ t("env.elapsed", { seconds: elapsed }) }}</div>
    </div>
    <v-alert v-if="view === 'cancelled'" type="info" variant="tonal" role="status" data-testid="env-cancelled">
      {{ t("env.cancelled") }}
      <template #append>
        <v-btn variant="text" size="small" data-testid="env-cancelled-retry" @click="environment.retry()">{{ t("env.checkAgain") }}</v-btn>
      </template>
    </v-alert>
    <template v-if="view === 'error' && state.load.error">
      <ErrorAlert :error="state.load.error" :title="t('common.loadFailed')" testid="env-error" @retry="environment.retry()" />
      <p v-if="state.load.error.kind === 'timeout'" class="text-caption" data-testid="env-timeout-hint">
        {{ t("env.timeoutHint", { seconds: limitSeconds }) }}
      </p>
    </template>

    <div v-if="previous" class="u-row" data-testid="env-previous">
      <v-chip size="small" label variant="tonal">{{ t("env.previous") }}</v-chip>
      <span class="text-caption">{{ previous }}</span>
    </div>
    <div :class="{ 'u-env-stale': previous !== null }" class="u-stack" data-testid="env-result">
      <EnvironmentSummary :data="state.data" />
      <p class="text-caption">{{ t("env.unverifiedBody") }}</p>

      <template v-if="state.data && current">
        <p v-if="state.data.activeProfile" class="text-body-2" data-testid="env-active">{{ t("env.active", { profile: state.data.activeProfile }) }}</p>
        <v-alert v-if="noteText" :type="note?.kind === 'server_choice' || note?.kind === 'choose' ? 'info' : 'warning'" variant="tonal" data-testid="env-note">
          {{ noteText }} <span v-if="noteUnchanged">{{ t("env.note.settingsUnchanged") }}</span>
        </v-alert>
        <v-alert v-if="view === 'partial'" type="warning" variant="tonal" data-testid="env-partial">
          {{ t("env.partial", { count: state.data.issues.length }) }}
        </v-alert>
        <p v-else-if="!detail && notices" class="text-body-2" data-testid="env-notice-count">{{ t("env.noticeCount", { count: notices }) }}</p>
      </template>

      <template v-if="state.data">
        <EnvironmentDetails
          v-if="props.detail"
          :data="state.data"
          :requested-profile="state.requestedProfile"
          :default-profile="savedDefault"
          open
        />
        <v-expansion-panels v-else variant="accordion" data-testid="env-more">
          <v-expansion-panel :title="t('env.showDetails')">
            <v-expansion-panel-text>
              <EnvironmentDetails :data="state.data" :requested-profile="state.requestedProfile" :default-profile="savedDefault" />
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </template>
    </div>
    <p v-if="props.detail && state.abandoned > 0" class="text-caption" data-testid="env-abandoned">{{ t("env.abandoned", { count: state.abandoned }) }}</p>
  </section>
</template>
