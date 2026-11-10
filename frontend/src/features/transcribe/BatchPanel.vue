<script setup lang="ts">
import { computed } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import LegacyLink from "../../components/LegacyLink.vue";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { countItems, type PreflightFailure } from "../../domain/batchSubmission";
import type { MessageKey } from "../../i18n";

/**
 * Expert ③ (S3-B1): send the selected, never-sent inputs one by one through the legacy
 * `POST /api/jobs` and show what each request got back. This is an *admission* summary only:
 * "accepted" is not "running" or "done", and many jobs are not followed here (S3-B2).
 */
const { core, batch, list, gate, job } = useTranscription();
const { t } = useI18n();

const run = computed(() => batch.value.run);
const running = computed(() => run.value?.phase === "running" || run.value?.phase === "stopping");
// `list`/`gate` are read so the target count follows selection and ledger changes.
const targets = computed(() => (list.value && gate.value ? core.batch.targets().length : 0));
const doorBusy = computed(() => !running.value && (gate.value.inFlight !== null || job.value.submit.phase === "pending"));
const canStart = computed(() => targets.value > 0 && !running.value && !doorBusy.value);
const counts = computed(() => (run.value ? countItems(run.value.items) : null));
const preflight = computed(() => batch.value.preflight);

function field(issue: PreflightFailure["issues"][number]): string {
  const name = t(`tx.job.field.${issue.field}` as MessageKey);
  const problem =
    issue.field === "input" || issue.field === "output"
      ? t(`tx.path.${issue.problem}` as MessageKey)
      : issue.field === "environment"
        ? t(`tx.env.${issue.problem}` as MessageKey)
        : t(`tx.job.problem.${issue.problem}` as MessageKey);
  return `${name}: ${problem}`;
}
function failureText(f: PreflightFailure): string {
  const parts = f.issues.map(field);
  if (f.sameInput) parts.push(t("tx.batch.sameInput"));
  return `${f.fileName} — ${parts.join(" / ")}`;
}
function start() {
  void core.batch.start();
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-batch-heading" data-testid="tx-batch">
    <h2 id="tx-batch-heading" class="u-subheading">{{ t("tx.batch.title") }}</h2>
    <p class="text-body-2">{{ t("tx.batch.explain") }}</p>
    <div class="u-row">
      <v-btn color="primary" :disabled="!canStart" :loading="running" data-testid="tx-batch-start" @click="start">{{ t("tx.batch.start", { count: targets }) }}</v-btn>
      <v-btn v-if="running" variant="outlined" color="warning" :disabled="run?.phase === 'stopping'" data-testid="tx-batch-stop" @click="core.batch.requestStop()">
        {{ t("tx.batch.stop") }}
      </v-btn>
    </div>
    <p v-if="doorBusy" class="text-caption" role="status" data-testid="tx-batch-door-busy">{{ t("tx.batch.doorBusy") }}</p>

    <v-alert v-if="preflight" type="warning" density="compact" role="status" data-testid="tx-batch-preflight">
      {{ t(`tx.batch.block.${preflight.block}` as MessageKey) }}
      <ul v-if="preflight.failures.length > 0" class="u-notes mt-1">
        <li v-for="f in preflight.failures" :key="f.clientFileId" data-testid="tx-batch-failure">{{ failureText(f) }}</li>
      </ul>
    </v-alert>

    <v-card v-if="run && counts" variant="outlined" class="pa-3 u-stack" data-testid="tx-batch-run">
      <p class="text-body-1" role="status" aria-live="polite" data-testid="tx-batch-phase">{{ t(`tx.batch.phase.${run.phase}` as MessageKey) }}</p>
      <p class="text-body-2" data-testid="tx-batch-counts">
        {{ t("tx.batch.counts", { accepted: counts.accepted, rejected: counts.rejected, unknown: counts.unknown, notSent: counts.notSent + counts.pending }) }}
      </p>
      <v-alert v-if="run.stopReason" :type="run.stopReason === 'unknown' ? 'warning' : 'info'" density="compact" data-testid="tx-batch-stop-reason">
        {{ t(`tx.batch.stopped.${run.stopReason}` as MessageKey) }}
        <template v-if="run.stopReason === 'unknown'" #append>
          <LegacyLink :label="t('app.openLegacy')" size="small" testid="tx-batch-legacy" />
        </template>
      </v-alert>
      <v-list density="compact" data-testid="tx-batch-items">
        <v-list-item v-for="item in run.items" :key="item.clientFileId" data-testid="tx-batch-item" :data-phase="item.phase">
          <v-list-item-title class="u-break">{{ item.fileName }}</v-list-item-title>
          <v-list-item-subtitle>
            {{ t(`tx.batch.item.${item.phase}` as MessageKey) }}<template v-if="item.jobId"> · {{ t("tx.job.id", { id: item.jobId }) }}</template>
          </v-list-item-subtitle>
          <ErrorAlert v-if="item.phase === 'rejected' && item.error" :error="item.error" :title="t('tx.job.rejected')" testid="tx-batch-item-error" />
        </v-list-item>
      </v-list>
      <p class="text-caption" data-testid="tx-batch-not-tracking">{{ t("tx.batch.notTracking") }}</p>
      <div v-if="!running" class="u-row">
        <v-btn variant="text" size="small" data-testid="tx-batch-dismiss" @click="core.batch.dismiss()">{{ t("tx.batch.dismiss") }}</v-btn>
      </div>
    </v-card>
    <ul class="text-caption u-notes">
      <li>{{ t("tx.batch.frozen") }}</li>
      <li data-testid="tx-batch-remember">{{ t("tx.batch.remember") }}</li>
      <li>{{ t("tx.batch.stopNote") }}</li>
    </ul>
  </section>
</template>
