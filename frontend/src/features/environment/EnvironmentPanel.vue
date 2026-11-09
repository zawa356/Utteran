<script setup lang="ts">
import { mdiRefresh } from "@mdi/js";
import { computed } from "vue";
import { PROFILES } from "../../api/legacy";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";

/** S2 slice: legacy GET /api/environment, read-only; never claims "ready" (G-01). */
const { environment } = useCore();
const state = useStore(environment.store);
const { t } = useI18n();

const DEFAULT = "__default__";
const profileItems = computed(() => [
  { title: t("env.profileDefault"), value: DEFAULT },
  ...PROFILES.map((name) => {
    const row = state.value.data?.profiles.find((p) => p.name === name);
    const suffix = !row || !row.exists ? t("env.missing") : row.compatible === false ? t("env.incompatible") : t("env.exists");
    return { title: `${name} — ${suffix}`, value: name };
  }),
]);
const selected = computed(() => state.value.requestedProfile ?? DEFAULT);
const pending = computed(() => state.value.load.phase === "pending");
// AppCore.start() loads the saved default profile; later visits (or a mode switch) keep the state.

function onSelect(value: string | null) {
  void environment.select(value === null || value === DEFAULT ? null : value);
}
function describe(option: { label: string; modelCount: number; deviceIds: string[] }) {
  return t("env.option", { label: option.label, models: option.modelCount, devices: option.deviceIds.join(", ") || t("env.none") });
}
</script>

<template>
  <section class="u-stack" aria-labelledby="env-heading" data-testid="environment-panel">
    <div class="u-row">
      <h2 id="env-heading" class="u-subheading">{{ t("env.title") }}</h2>
      <v-chip size="small" label color="warning" variant="outlined" data-testid="env-readiness">{{ t("env.unverified") }}</v-chip>
    </div>
    <div class="u-row">
      <v-select
        :label="t('env.profile')"
        :items="profileItems"
        :model-value="selected"
        class="u-w-sm"
        data-testid="env-profile"
        @update:model-value="onSelect"
      />
      <v-btn variant="tonal" :prepend-icon="mdiRefresh" :disabled="pending" data-testid="env-redetect" @click="environment.redetect()">
        {{ t("env.redetect") }}
      </v-btn>
      <v-btn v-if="pending" variant="text" data-testid="env-cancel" @click="environment.cancel()">{{ t("env.cancel") }}</v-btn>
    </div>
    <div v-if="pending" role="status" aria-live="polite" data-testid="env-loading">
      <v-progress-linear indeterminate color="primary" />
      <span class="text-body-2">{{ t("env.loading") }}</span>
    </div>
    <p v-if="state.load.phase === 'cancelled'" class="text-body-2" role="status" data-testid="env-cancelled">{{ t("env.cancelled") }}</p>
    <ErrorAlert
      v-if="state.load.phase === 'error' && state.load.error"
      :error="state.load.error"
      :title="t('common.loadFailed')"
      testid="env-error"
      @retry="environment.retry()"
    />
    <template v-if="state.data">
      <p class="text-body-2" data-testid="env-active">
        {{ state.data.activeProfile ? t("env.active", { profile: state.data.activeProfile }) : t("env.noActive") }}
      </p>
      <p class="text-caption">{{ t("env.unverifiedBody") }}</p>
      <div>
        <h3 class="u-subheading">{{ t("env.asr") }}</h3>
        <ul data-testid="env-asr">
          <li v-for="o in state.data.asr" :key="o.id">{{ describe(o) }}</li>
          <li v-if="state.data.asr.length === 0">{{ t("env.none") }}</li>
        </ul>
      </div>
      <div>
        <h3 class="u-subheading">{{ t("env.diarization") }}</h3>
        <ul data-testid="env-diarization">
          <li v-for="o in state.data.diarization" :key="o.id">{{ describe(o) }}</li>
          <li v-if="state.data.diarization.length === 0">{{ t("env.none") }}</li>
        </ul>
      </div>
      <v-alert v-if="state.data.warnings.length" type="warning" :title="t('env.warnings')">
        <ul><li v-for="(w, i) in state.data.warnings" :key="i">{{ w }}</li></ul>
      </v-alert>
      <v-alert v-if="state.data.errors.length" type="warning" :title="t('env.errors')" data-testid="env-messages">
        <ul><li v-for="(e, i) in state.data.errors" :key="i">{{ e }}</li></ul>
      </v-alert>
    </template>
  </section>
</template>
