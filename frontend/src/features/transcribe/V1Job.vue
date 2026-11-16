<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCodeText, useV1 } from "../../composables/v1";
import { summarize } from "../../domain/v1/settings";
import type { MessageKey } from "../../i18n";

/**
 * S5-A (`--next-ui-v1`): the only start path in v1 mode, shared by Normal and Expert.
 * Two steps: "check" (B5 validation + B7 resolution) → "submit" (B7 job, the same frozen request).
 * The status is the last `GET /api/v1/jobs/{id}`; "accepted" is never "started" or "done".
 */
const { v1, job: s, evaluation, block, stale } = useV1();
const { t, reason, issue, status, formIssue } = useCodeText();

const prepared = computed(() => s.value.prepared);
const resolution = computed(() => prepared.value?.resolution ?? null);
const spec = computed(() => resolution.value?.files[0]?.spec ?? null);
const busy = computed(() => s.value.step === "validating" || s.value.step === "resolving" || s.value.step === "submitting");
const canCheck = computed(() => evaluation.value.session !== null && block.value === null);

// Client clock for the token lifetime (the server decides; this only disables the button).
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
const secondsLeft = computed(() => (resolution.value ? Math.max(0, Math.floor((Date.parse(resolution.value.expiresAt) - clock.value) / 1000)) : 0));
const expired = computed(() => !!resolution.value && secondsLeft.value <= 0);
// The domain decides (resolved, fresh, unexpired, no open cancel); `s` and the clock make it re-evaluate.
const canSubmit = computed(() => {
  void [s.value, clock.value, evaluation.value];
  return v1.job.canSubmit();
});

const effective = computed(() => (prepared.value ? summarize(prepared.value.effective) : null));
const stageTitle = computed(() => (s.value.failedAt ? t(`v1.stage.${s.value.failedAt}` as MessageKey) : ""));
const view = computed(() => s.value.job);
const queue = computed(() => s.value.queue.view);
const cancellable = computed(() => !!view.value && s.value.tracking !== "final" && view.value.status !== "cancelling");
// An open (pending/unknown) cancel intent pins the shown job: no follow/dismiss until it is settled (PR #48 P1).
const switchBlocked = computed(() => {
  void s.value;
  return v1.job.switchBlocked();
});
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-job-heading" data-testid="v1-job">
    <h2 id="v1-job-heading" class="u-subheading">{{ t("v1.job.title") }}</h2>
    <p class="text-caption">{{ t("v1.job.explain") }}</p>

    <div v-if="evaluation.issues.length > 0" data-testid="v1-missing">
      <p class="text-body-2">{{ t("tx.job.missing") }}</p>
      <ul class="text-body-2 u-notes">
        <li v-for="i in evaluation.issues" :key="i.field + i.problem">{{ formIssue(i) }}</li>
      </ul>
    </div>
    <p v-if="evaluation.selectedCount > 1" class="text-caption" data-testid="v1-single-only">{{ t("v1.job.singleOnly", { count: evaluation.selectedCount }) }}</p>
    <v-alert v-if="evaluation.workerLimits.length > 0" type="warning" density="compact" data-testid="v1-worker-limits">
      {{ t("v1.job.workerLimits") }}
      <ul class="u-notes">
        <li v-for="c in evaluation.workerLimits" :key="c">{{ reason(c) }}</li>
      </ul>
    </v-alert>
    <v-alert v-if="block" type="info" density="compact" role="status" data-testid="v1-block">{{ t(`v1.block.${block}` as MessageKey) }}</v-alert>

    <div class="u-row">
      <v-btn color="primary" variant="outlined" :disabled="!canCheck" :loading="s.step === 'validating' || s.step === 'resolving'" data-testid="v1-check" @click="v1.job.prepare()">{{ t("v1.job.check") }}</v-btn>
      <v-btn color="primary" :disabled="!canSubmit" :loading="s.step === 'submitting'" data-testid="v1-submit" @click="v1.job.submit()">{{ t("v1.job.submit") }}</v-btn>
      <v-btn v-if="prepared && !busy && s.step !== 'unknown' && s.step !== 'accepted'" variant="text" size="small" data-testid="v1-reset" @click="v1.job.reset()">{{ t("v1.job.reset") }}</v-btn>
    </div>
    <p v-if="s.step === 'validating'" role="status" class="text-body-2" data-testid="v1-validating">{{ t("v1.job.validating") }}</p>
    <p v-if="s.step === 'resolving'" role="status" class="text-body-2" data-testid="v1-resolving">{{ t("v1.job.resolving") }}</p>
    <p v-if="s.step === 'submitting'" role="status" class="text-body-2" data-testid="v1-submitting">{{ t("v1.job.submitting") }}</p>

    <!-- The frozen request (what "check" and "submit" use) -->
    <v-card v-if="prepared && effective" variant="outlined" class="pa-3 u-stack" data-testid="v1-prepared">
      <p class="text-body-2 u-break" data-testid="v1-prepared-summary">
        {{ t("v1.job.prepared", { file: fileName(prepared.inputPath) ?? "?", preset: prepared.presetName, policy: prepared.policyName }) }}
      </p>
      <p class="text-caption u-break">{{ t("v1.lib.summary", { backend: effective.backend, model: effective.model, language: effective.language, formats: effective.formats.join(", ") }) }}</p>
      <p class="text-caption u-break" data-testid="v1-prepared-output">{{ t("tx.job.outputFolder", { folder: prepared.outputDir }) }}</p>
      <p v-if="prepared.validation" class="text-caption" data-testid="v1-validated">{{ t("v1.job.validated") }}</p>
      <template v-if="spec && resolution">
        <v-alert type="success" variant="tonal" density="compact" data-testid="v1-resolution">
          <p>{{ t("v1.job.resolved") }}</p>
          <p class="text-caption u-break" data-testid="v1-resolution-spec">
            {{ t("v1.job.spec", { profile: spec.profileId, recipe: spec.recipeId, model: spec.modelId, revision: spec.modelRevision, device: spec.device, compute: spec.computeType, confidence: spec.confidence }) }}
          </p>
          <p class="text-caption" data-testid="v1-resolution-evidence">{{ t("v1.job.evidence", { kinds: spec.evidenceKinds.join(", ") || "-" }) }}</p>
          <p v-if="spec.fallbackCandidateIds.length > 0" class="text-caption">{{ t("v1.job.fallbackRecorded", { count: spec.fallbackCandidateIds.length }) }}</p>
          <ul v-if="spec.warnings.length > 0" class="text-caption u-notes" data-testid="v1-resolution-warnings">
            <li v-for="w in spec.warnings" :key="w.path + w.code">{{ issue(w) }}</li>
          </ul>
          <p v-if="s.step === 'resolved'" class="text-caption" data-testid="v1-expires">{{ expired ? t("v1.job.expired") : t("v1.job.expiresIn", { seconds: secondsLeft }) }}</p>
        </v-alert>
      </template>
      <v-alert v-if="stale && s.step === 'resolved'" type="warning" density="compact" data-testid="v1-stale">{{ t("v1.job.stale") }}</v-alert>
    </v-card>

    <!-- Refusals (no job was created) -->
    <template v-if="s.step === 'rejected'">
      <v-alert v-if="s.mismatch" type="error" data-testid="v1-mismatch">{{ t("v1.job.mismatch") }}</v-alert>
      <template v-else-if="s.error">
        <ErrorAlert :error="s.error" :title="t('v1.job.rejected', { stage: stageTitle })" testid="v1-rejected" />
        <ul v-if="s.error.issues.length > 0" class="text-body-2 u-notes" data-testid="v1-rejected-issues">
          <li v-for="i in s.error.issues" :key="i.path + i.code">{{ issue(i) }}</li>
        </ul>
      </template>
      <p class="text-caption">{{ t("v1.job.rejectedNote") }}</p>
    </template>

    <!-- Unknown outcome: the same key + frozen body only, on request -->
    <v-alert v-if="s.step === 'unknown'" type="warning" :title="t('v1.unknown.title', { stage: stageTitle })" data-testid="v1-unknown">
      <p>{{ s.failedAt === "submission" ? t("v1.unknown.submission") : t("v1.unknown.other") }}</p>
      <p class="text-caption">{{ t("v1.unknown.memory") }}</p>
      <div class="u-row mt-2">
        <v-btn variant="outlined" size="small" data-testid="v1-recheck" @click="v1.job.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
        <v-btn variant="text" size="small" data-testid="v1-recent-open" @click="v1.job.loadRecent()">{{ t("v1.recent.reload") }}</v-btn>
        <v-btn variant="text" size="small" data-testid="v1-unknown-acknowledge" @click="v1.job.reset()">{{ t("v1.unknown.acknowledge") }}</v-btn>
      </div>
    </v-alert>

    <!-- The durable job (GET is the authority) -->
    <v-card v-if="view || s.tracking === 'lost'" variant="outlined" class="pa-3 u-stack" data-testid="v1-status-card">
      <p v-if="s.origin === 'submitted'" class="text-caption" data-testid="v1-accepted-note">{{ t("v1.job.acceptedNote") }}</p>
      <p v-else-if="s.origin === 'followed'" class="text-caption" data-testid="v1-followed-note">{{ t("v1.job.followedNote") }}</p>
      <template v-if="view">
        <p class="text-body-1" role="status" aria-live="polite" data-testid="v1-status">{{ status(view.status) }}</p>
        <p class="text-caption" data-testid="v1-job-id">{{ t("v1.job.id", { id: view.id, revision: view.revision }) }}</p>
        <p class="text-caption" data-testid="v1-job-reason">{{ t("v1.job.reason", { reason: reason(view.reasonCode) }) }}</p>
        <p v-if="view.held" class="text-caption">{{ t("v1.job.held") }}</p>
        <p v-if="view.blockReason" class="text-body-2" data-testid="v1-job-block">{{ t("v1.job.blocked", { reason: reason(view.blockReason) }) }}</p>
        <p v-if="view.recoveryRequired" class="text-body-2" data-testid="v1-job-recovery">{{ t("v1.job.recovery", { reason: reason(view.recoveryRequired) }) }}</p>
        <p v-if="view.errorCode" class="text-body-2" data-testid="v1-job-error">{{ t("v1.job.error", { reason: reason(view.errorCode) }) }}</p>
        <p v-if="view.artifactCount !== null" class="text-body-2" data-testid="v1-job-artifacts">{{ t("v1.job.artifacts", { count: view.artifactCount }) }}</p>
        <p class="text-caption">{{ t("v1.job.noProgress") }}</p>
      </template>
      <p v-if="s.tracking === 'lost'" class="text-body-2" data-testid="v1-lost">{{ t("v1.job.lost") }}</p>
      <p v-if="s.tracking === 'stopped'" class="text-caption" data-testid="v1-stopped">{{ t("v1.job.stopped") }}</p>
      <p v-if="s.poll.failures > 0" class="text-caption" data-testid="v1-poll-failing">{{ t("tx.job.poll.failing", { count: s.poll.failures }) }}</p>
      <v-alert v-if="s.cancel.phase !== 'idle'" :type="s.cancel.phase === 'rejected' || s.cancel.phase === 'unknown' ? 'warning' : 'info'" density="compact" role="status" data-testid="v1-cancel-state">
        {{ t(`v1.cancel.${s.cancel.phase}` as MessageKey) }}
        <div v-if="s.cancel.jobId" class="text-caption" data-testid="v1-cancel-target">{{ t("v1.cancel.target", { id: s.cancel.jobId }) }}</div>
        <template v-if="s.cancel.phase === 'unknown'" #append>
          <v-btn variant="text" size="small" data-testid="v1-cancel-recheck" @click="v1.job.recheckCancel()">{{ t("v1.unknown.recheck") }}</v-btn>
          <v-btn variant="text" size="small" data-testid="v1-cancel-discard" @click="v1.job.discardCancel()">{{ t("v1.cancel.discard") }}</v-btn>
        </template>
      </v-alert>
      <ErrorAlert v-if="s.cancel.phase === 'rejected' && s.cancel.error" :error="s.cancel.error" testid="v1-cancel-error" />
      <div class="u-row">
        <v-btn v-if="cancellable" variant="outlined" color="error" :loading="s.cancel.phase === 'pending'" :disabled="s.cancel.phase === 'pending' || s.cancel.phase === 'unknown'" data-testid="v1-cancel" @click="v1.job.cancel()">{{ t("v1.cancel.button") }}</v-btn>
        <v-btn variant="text" size="small" data-testid="v1-refresh" @click="v1.job.refresh()">{{ t("v1.job.refresh") }}</v-btn>
        <v-btn v-if="s.tracking !== 'active'" variant="text" size="small" :disabled="switchBlocked !== null" data-testid="v1-dismiss" @click="v1.job.dismiss()">{{ t("tx.job.dismiss") }}</v-btn>
      </div>
      <p class="text-caption">{{ t("v1.job.leaveNote") }}</p>
    </v-card>

    <!-- The v1 queue (paused after first start / recovery; resumed only explicitly) -->
    <v-card variant="outlined" class="pa-3 u-stack" data-testid="v1-queue">
      <h3 class="u-subheading">{{ t("v1.queue.title") }}</h3>
      <ErrorAlert v-if="s.queue.phase === 'error' && s.queue.error" :error="s.queue.error" testid="v1-queue-error" @retry="v1.job.loadQueue()" />
      <template v-if="queue">
        <p class="text-body-2" role="status" data-testid="v1-queue-dispatch">{{ queue.dispatch === "paused" ? t("v1.queue.paused") : t("v1.queue.running") }}</p>
        <p class="text-caption" data-testid="v1-queue-count">{{ t("v1.queue.count", { count: queue.items.length, revision: queue.revision }) }}</p>
        <p v-if="queue.dispatch === 'paused'" class="text-caption">{{ t("v1.queue.pausedNote") }}</p>
      </template>
      <v-alert v-if="s.control.phase === 'conflict'" type="info" density="compact" data-testid="v1-queue-conflict">{{ t("v1.queue.conflict") }}</v-alert>
      <ErrorAlert v-if="s.control.phase === 'rejected' && s.control.error" :error="s.control.error" testid="v1-queue-control-error" />
      <v-alert v-if="s.control.phase === 'unknown'" type="warning" density="compact" data-testid="v1-queue-unknown">
        {{ t("v1.queue.unknown") }}
        <template #append>
          <v-btn variant="text" size="small" data-testid="v1-queue-recheck" @click="v1.job.recheckResume()">{{ t("v1.unknown.recheck") }}</v-btn>
        </template>
      </v-alert>
      <div class="u-row">
        <v-btn
          v-if="queue?.dispatch === 'paused'"
          variant="outlined"
          :loading="s.control.phase === 'pending'"
          :disabled="s.control.phase === 'pending' || s.control.phase === 'unknown'"
          data-testid="v1-queue-resume"
          @click="v1.job.resumeQueue()"
        >
          {{ t("v1.queue.resume") }}
        </v-btn>
        <v-btn variant="text" size="small" :loading="s.queue.phase === 'pending'" data-testid="v1-queue-reload" @click="v1.job.loadQueue()">{{ t("v1.queue.reload") }}</v-btn>
      </div>
    </v-card>

    <!-- Durable jobs on the server (re-display after a restart; not a claim about an unknown send) -->
    <v-card variant="outlined" class="pa-3 u-stack" data-testid="v1-recent">
      <h3 class="u-subheading">{{ t("v1.recent.title") }}</h3>
      <p class="text-caption">{{ t("v1.recent.explain") }}</p>
      <ErrorAlert v-if="s.recent.phase === 'error' && s.recent.error" :error="s.recent.error" testid="v1-recent-error" />
      <p v-if="switchBlocked" class="text-caption" role="status" data-testid="v1-switch-blocked">{{ t(`v1.switch.${switchBlocked}` as MessageKey) }}</p>
      <p v-if="s.recent.phase === 'done' && s.recent.items.length === 0" class="text-body-2" data-testid="v1-recent-empty">{{ t("v1.recent.empty") }}</p>
      <v-list v-if="s.recent.items.length > 0" density="compact" data-testid="v1-recent-items">
        <v-list-item v-for="item in s.recent.items" :key="item.id" data-testid="v1-recent-item" :data-status="item.status">
          <v-list-item-title class="u-break">{{ status(item.status) }} · {{ t("v1.job.id", { id: item.id, revision: item.revision }) }}</v-list-item-title>
          <v-list-item-subtitle>{{ item.createdAt }}</v-list-item-subtitle>
          <template #append>
            <v-btn variant="text" size="small" :disabled="view?.id === item.id || switchBlocked !== null" data-testid="v1-recent-follow" @click="v1.job.follow(item.id)">{{ t("v1.recent.follow") }}</v-btn>
          </template>
        </v-list-item>
      </v-list>
      <div class="u-row">
        <v-btn variant="text" size="small" :loading="s.recent.phase === 'pending'" data-testid="v1-recent-reload" @click="v1.job.loadRecent()">{{ t("v1.recent.reload") }}</v-btn>
      </div>
    </v-card>
  </section>
</template>
