<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { BatchIssue } from "../../domain/v1/batchJobs";
import type { MessageKey } from "../../i18n";

/**
 * S5-B (`--next-ui-v1`): the selected rows as one atomic B7 batch. Two steps like the single job:
 * "check all" (B5 validation + B7 resolution for every file) → "submit" (one request, all or none).
 * After acceptance the jobs are followed in the board below; this panel does not poll.
 */
const core = useCore();
const batch = core.v1!.batch;
const board = core.v1!.board;
const s = useStore(batch.store);
const queueGate = useStore(core.v1!.queueGate.store);
const inputs = useStore(core.inputs.store);
const form = useStore(core.transcription.store);
const library = useStore(core.v1!.library.store);
const { t, issue } = useCodeText();

const evaluation = computed(() => {
  void [inputs.value, form.value, library.value];
  return batch.evaluate();
});
const busy = computed(() => s.value.step === "validating" || s.value.step === "resolving" || s.value.step === "submitting");
const clock = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
watch(
  () => s.value.step === "resolved",
  (resolved) => {
    if (timer) clearInterval(timer);
    timer = resolved ? setInterval(() => (clock.value = Date.now()), 1000) : null;
    clock.value = Date.now();
  },
  { immediate: true },
);
onUnmounted(() => timer && clearInterval(timer));
const canPrepare = computed(() => {
  void [s.value, evaluation.value];
  return batch.canPrepare();
});
const stale = computed(() => {
  void [s.value, evaluation.value];
  return batch.isStale();
});
const expired = computed(() => {
  void [s.value, clock.value];
  return batch.isExpired();
});
const canSubmit = computed(() => {
  void [s.value, clock.value, evaluation.value, queueGate.value];
  return batch.canSubmit();
});
const count = computed(() => s.value.prepared?.files.length ?? evaluation.value.count);
const stageTitle = computed(() => (s.value.failedAt ? t(`v1.stage.${s.value.failedAt}` as MessageKey) : ""));
const nameOf = (rowId: string) => fileName(inputs.value.rows.find((r) => r.clientFileId === rowId)?.inputPath ?? "") ?? "?";
function issueText(i: BatchIssue): string {
  switch (i.field) {
    case "preset":
    case "policy":
      return `${t(`v1.field.${i.field}` as MessageKey)}: ${t(`v1.problem.${i.problem}` as MessageKey)}`;
    case "rows":
      return t(`v1.batch.issue.${i.problem}` as MessageKey);
    case "device":
      return t("v1.batch.rowIssue.device", { file: nameOf(i.rowId) });
    default:
      return t(`v1.batch.rowIssue.${i.field}` as MessageKey, { file: nameOf(i.rowId), problem: t(`tx.path.${i.problem}` as MessageKey) });
  }
}
// A newly accepted batch appears in the board without waiting for the next poll.
watch(
  () => s.value.step,
  (step) => {
    if (step !== "accepted") return;
    void board.refresh();
    // The submission moved the queue revision: the queue panel must not keep a stale one
    // (its explicit "resume" is a CAS on that revision).
    void core.v1!.job.loadQueue();
  },
);
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-batch-heading" data-testid="v1-batch" :data-step="s.step">
    <h2 id="v1-batch-heading" class="u-subheading">{{ t("v1.batch.title") }}</h2>
    <p class="text-caption">{{ t("v1.batch.explain") }}</p>
    <p class="text-body-2" data-testid="v1-batch-count">{{ t("v1.batch.count", { count: evaluation.count }) }}</p>
    <ul v-if="evaluation.issues.length > 0" class="text-body-2 u-notes" data-testid="v1-batch-issues">
      <li v-for="(i, index) in evaluation.issues" :key="index">{{ issueText(i) }}</li>
    </ul>

    <div class="u-row">
      <v-btn color="primary" variant="outlined" :disabled="!canPrepare" :loading="s.step === 'validating' || s.step === 'resolving'" data-testid="v1-batch-check" @click="batch.prepare()">{{ t("v1.batch.check") }}</v-btn>
      <v-btn color="primary" :disabled="!canSubmit" :loading="s.step === 'submitting'" data-testid="v1-batch-submit" @click="batch.submit()">{{ t("v1.batch.submit", { count }) }}</v-btn>
      <v-btn v-if="s.prepared && !busy && s.step !== 'unknown'" variant="text" size="small" data-testid="v1-batch-reset" @click="batch.reset()">{{ t("v1.batch.reset") }}</v-btn>
    </div>
    <p v-if="s.step === 'validating'" role="status" class="text-body-2">{{ t("v1.batch.validating", { count }) }}</p>
    <p v-if="s.step === 'resolving'" role="status" class="text-body-2">{{ t("v1.batch.resolving", { count }) }}</p>
    <p v-if="s.step === 'submitting'" role="status" class="text-body-2">{{ t("v1.batch.submitting", { count }) }}</p>

    <v-card v-if="s.prepared && s.step !== 'accepted'" variant="outlined" class="pa-3 u-stack" data-testid="v1-batch-prepared">
      <ul class="text-body-2 u-notes">
        <li v-for="(file, index) in s.prepared.files" :key="file.fileId" class="u-break" data-testid="v1-batch-file" :data-mismatch="s.mismatched.includes(file.rowId)">
          {{ t("v1.batch.file", { file: fileName(file.inputPath) ?? "?", folder: file.outputDir }) }}
          <span v-if="((s.prepared.session.files as { overrides: unknown[] }[])[index]?.overrides.length ?? 0) > 0" class="text-caption">
            · {{ t("v1.batch.fileOverrides", { count: (s.prepared.session.files as { overrides: unknown[] }[])[index]!.overrides.length }) }}
          </span>
          <ul v-if="s.prepared.validation?.files[index]" class="text-caption u-notes" data-testid="ea-batch-validation">
            <li v-for="item in [...s.prepared.validation.files[index]!.issues, ...s.prepared.validation.files[index]!.warnings]" :key="item.path + item.code">{{ issue(item) }}</li>
          </ul>
        </li>
      </ul>
      <v-alert v-if="s.step === 'resolved' && !stale && !expired" type="success" variant="tonal" density="compact" data-testid="v1-batch-resolved">{{ t("v1.batch.resolved", { count }) }}</v-alert>
      <v-alert v-if="s.step === 'resolved' && stale" type="warning" density="compact" data-testid="v1-batch-stale">{{ t("v1.batch.stale") }}</v-alert>
      <v-alert v-else-if="s.step === 'resolved' && expired" type="warning" density="compact" data-testid="v1-batch-expired">{{ t("v1.batch.expired") }}</v-alert>
    </v-card>

    <template v-if="s.step === 'rejected'">
      <v-alert v-if="s.mismatched.length > 0" type="error" data-testid="v1-batch-mismatch">{{ t("v1.batch.mismatch") }}</v-alert>
      <template v-else-if="s.error">
        <ErrorAlert :error="s.error" :title="t('v1.batch.rejected', { stage: stageTitle })" testid="v1-batch-rejected" />
        <ul v-if="s.error.issues.length > 0" class="text-body-2 u-notes" data-testid="v1-batch-rejected-issues">
          <li v-for="i in s.error.issues" :key="i.path + i.code">{{ issue(i) }}</li>
        </ul>
      </template>
    </template>

    <v-alert v-if="s.step === 'unknown'" type="warning" data-testid="v1-batch-unknown">
      <p>{{ t("v1.batch.unknown") }}</p>
      <div class="u-row mt-2">
        <v-btn variant="outlined" size="small" data-testid="v1-batch-recheck" @click="batch.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
        <v-btn variant="text" size="small" data-testid="v1-batch-giveup" @click="batch.reset()">{{ t("v1.batch.giveUp") }}</v-btn>
      </div>
    </v-alert>

    <v-alert v-if="s.step === 'accepted'" type="success" variant="tonal" data-testid="v1-batch-accepted">{{ t("v1.batch.accepted", { count: s.jobs.length }) }}</v-alert>
  </section>
</template>
