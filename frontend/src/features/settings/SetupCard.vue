<script setup lang="ts">
import { computed, onMounted } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import LegacyLink from "../../components/LegacyLink.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { wizardSummary } from "../../domain/wizard";

/**
 * S1 hand-over to the real (legacy) setup wizard: a read-only status and a link to `/`.
 * Nothing here starts the wizard, a setup job or the v1 setup (S4/S5).
 */
const { wizard } = useCore();
const state = useStore(wizard.store);
const { t } = useI18n();
const summary = computed(() => (state.value.data ? wizardSummary(state.value.data) : null));
const summaryText = computed(() => {
  if (summary.value === "completed") return t("setup.completed");
  if (summary.value === "resumable") return t("setup.resumable");
  if (summary.value === "not_started") return t("setup.notStarted");
  return t("setup.loading");
});
onMounted(() => void wizard.load());
</script>

<template>
  <v-card :title="t('setup.title')" class="u-card" data-testid="setup-card">
    <v-card-text class="u-stack">
      <p>{{ t("setup.body") }}</p>
      <p v-if="state.load.phase !== 'error'" role="status" class="text-body-2" data-testid="setup-status">
        {{ t("setup.status", { status: summaryText }) }}
      </p>
      <ErrorAlert
        v-if="state.load.phase === 'error' && state.load.error"
        :error="state.load.error"
        :title="t('setup.loadFailed')"
        testid="setup-load-error"
        @retry="wizard.load()"
      />
      <div class="u-row">
        <LegacyLink :label="t('setup.open')" variant="outlined" testid="setup-open-legacy" />
      </div>
    </v-card-text>
  </v-card>
</template>
