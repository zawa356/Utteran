<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { fileName, isTerminal, START_TIMEOUT_MS } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import LegacyLink from "../../components/LegacyLink.vue";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import type { MessageKey } from "../../i18n";

/**
 * S3-A run panel. The status shown is always the last `GET /api/jobs/{id}` answer (SSE only
 * advances the stage display); "cancelled" only after GET says so.
 */
const { core, job, evaluation, block, primary } = useTranscription();
const { t } = useI18n();

const s = computed(() => job.value);
const pending = computed(() => s.value.submit.phase === "pending");
const canStart = computed(() => evaluation.value.request !== null && block.value === null && !pending.value);
const missing = computed(() =>
  evaluation.value.issues.map((i) => {
    const field = t(`tx.job.field.${i.field}` as MessageKey);
    const problem = i.field === "input" || i.field === "output" ? t(`tx.path.${i.problem}` as MessageKey) : i.field === "environment" ? t(`tx.env.${i.problem}` as MessageKey) : t(`tx.job.problem.${i.problem}` as MessageKey);
    return `${field}: ${problem}`;
  }),
);
const view = computed(() => s.value.job);
const terminal = computed(() => (view.value ? isTerminal(view.value.status) : false));
const statusText = computed(() => {
  if (s.value.tracking === "lost") return t("tx.job.status.lost");
  return view.value ? t(`tx.job.status.${view.value.status}` as MessageKey) : "";
});
const stageText = computed(() => {
  const p = view.value?.progress;
  if (!p || p.stage === null) return t("tx.job.noStage");
  const stage = t(`tx.job.stage.${p.stage}` as MessageKey);
  return p.ratio === null ? t("tx.job.stage", { stage }) : t("tx.job.stageRatio", { stage, percent: Math.round(p.ratio * 100) });
});
const snapshotText = computed(() => {
  const r = s.value.snapshot?.request;
  if (!r) return "";
  return t("tx.job.snapshotDetail", {
    file: fileName(r.inputPath) ?? "?",
    folder: r.outputDir,
    backend: r.asrBackend,
    model: r.asrModel,
    device: r.asrDevice,
    language: t(`tx.lang.${r.language}` as MessageKey),
    formats: r.formats.join(", "),
  });
});
const rejectedText = computed(() => {
  const kind = s.value.submit.error?.kind;
  return kind === "conflict" ? t("tx.job.rejected.conflict") : kind === "validation" ? t("tx.job.rejected.validation") : t("tx.job.rejected");
});
const shownOutputs = computed(() => view.value?.outputNames.slice(0, 8) ?? []);
const moreOutputs = computed(() => Math.max(0, (view.value?.outputCount ?? 0) - shownOutputs.value.length));

// Client-clock elapsed time while the start request is waiting.
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
const elapsed = computed(() => Math.max(0, Math.floor((clock.value - (s.value.submit.startedAt ?? clock.value)) / 1000)));

function start() {
  const request = evaluation.value.request;
  if (request) void core.job.submit(request, { clientFileId: primary.value?.clientFileId ?? null });
}

const openFailed = ref(false);
const opening = ref(false);
async function openFolder() {
  const folder = s.value.snapshot?.request.outputDir;
  if (!folder || opening.value) return;
  opening.value = true;
  openFailed.value = false;
  try {
    await core.legacy.openFolder(folder);
  } catch {
    openFailed.value = true;
  } finally {
    opening.value = false;
  }
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-job-heading" data-testid="tx-job">
    <h2 id="tx-job-heading" class="u-subheading">{{ t("tx.job.title") }}</h2>

    <div v-if="missing.length > 0" data-testid="tx-missing">
      <p class="text-body-2">{{ t("tx.job.missing") }}</p>
      <ul class="text-body-2 u-notes">
        <li v-for="m in missing" :key="m">{{ m }}</li>
      </ul>
    </div>
    <v-alert v-if="block" type="info" role="status" data-testid="tx-block">
      {{ t(`tx.job.block.${block}` as MessageKey) }}
      <template v-if="block === 'same_input'" #append>
        <LegacyLink :label="t('app.openLegacy')" size="small" testid="tx-block-legacy" />
      </template>
    </v-alert>
    <div class="u-row">
      <v-btn color="primary" :disabled="!canStart" :loading="pending" data-testid="tx-start" @click="start">{{ t("tx.job.start") }}</v-btn>
    </div>

    <div v-if="pending" role="status" data-testid="tx-submitting">
      <p class="text-body-2">{{ t("tx.job.submitting") }}</p>
      <p class="text-caption">{{ t("tx.job.elapsed", { seconds: elapsed }) }} · {{ t("env.limit", { seconds: START_TIMEOUT_MS / 1000 }) }}</p>
    </div>

    <template v-if="s.submit.phase === 'rejected' && s.submit.error">
      <ErrorAlert :error="s.submit.error" :title="rejectedText" testid="tx-rejected" />
      <v-btn variant="text" size="small" data-testid="tx-dismiss" @click="core.job.dismiss()">{{ t("tx.job.dismiss") }}</v-btn>
    </template>

    <v-alert v-if="s.submit.phase === 'unknown'" type="warning" :title="t('tx.job.unknown.title')" data-testid="tx-unknown">
      <p>{{ t("tx.job.unknown.body") }}</p>
      <p class="text-caption">{{ t("tx.job.unknown.queueNote") }}</p>
      <p v-if="s.queueCheck.phase === 'done'" data-testid="tx-queue-result">{{ t("tx.job.unknown.queueResult", { count: s.queueCheck.active ?? 0 }) }}</p>
      <ErrorAlert v-if="s.queueCheck.phase === 'error' && s.queueCheck.error" :error="s.queueCheck.error" testid="tx-queue-error" />
      <div class="u-row mt-2">
        <v-btn variant="outlined" size="small" :loading="s.queueCheck.phase === 'pending'" data-testid="tx-check-queue" @click="core.job.checkQueue()">
          {{ t("tx.job.unknown.checkQueue") }}
        </v-btn>
        <LegacyLink :label="t('app.openLegacy')" size="small" testid="tx-unknown-legacy" />
        <v-btn variant="text" size="small" data-testid="tx-acknowledge" @click="core.job.dismiss()">{{ t("tx.job.unknown.acknowledge") }}</v-btn>
      </div>
    </v-alert>

    <v-card v-if="view || s.tracking === 'lost'" variant="outlined" class="pa-3 u-stack" data-testid="tx-status-card">
      <p class="text-body-1" role="status" aria-live="polite" data-testid="tx-status">{{ statusText }}</p>
      <p v-if="view" class="text-caption" data-testid="tx-job-id">{{ t("tx.job.id", { id: view.id }) }}</p>
      <template v-if="s.tracking === 'active' && view">
        <v-progress-linear
          :indeterminate="view.progress.ratio === null"
          :model-value="view.progress.ratio === null ? undefined : view.progress.ratio * 100"
          :aria-label="stageText"
          data-testid="tx-progress"
        />
        <p class="text-body-2" data-testid="tx-stage">{{ stageText }}</p>
        <p v-if="view.stalled" class="text-caption" data-testid="tx-stalled">{{ t("tx.job.stalled") }}</p>
        <p v-if="s.poll.failures > 0" class="text-caption" data-testid="tx-poll-failing">{{ t("tx.job.poll.failing", { count: s.poll.failures }) }}</p>
        <p class="text-caption" data-testid="tx-live">{{ s.sse === "open" ? t("tx.job.live.open") : s.sse === "closed" || s.sse === "unsupported" ? t("tx.job.live.fallback") : "" }}</p>
      </template>
      <v-alert v-if="s.cancel.phase !== 'idle' && (s.tracking === 'active' || s.cancel.phase === 'too_late')" :type="s.cancel.phase === 'failed' ? 'error' : 'info'" density="compact" role="status" data-testid="tx-cancel-state">
        {{ t(s.cancel.phase === "pending" ? "tx.job.cancel.pending" : (`tx.job.cancel.${s.cancel.phase}` as MessageKey)) }}
      </v-alert>
      <div v-if="s.tracking === 'active'" class="u-row">
        <v-btn
          variant="outlined"
          color="error"
          :disabled="s.cancel.phase === 'pending' || s.cancel.phase === 'requested'"
          :loading="s.cancel.phase === 'pending'"
          data-testid="tx-cancel"
          @click="core.job.cancel()"
        >
          {{ t("tx.job.cancel") }}
        </v-btn>
      </div>

      <template v-if="terminal && view">
        <p v-if="view.guidance" class="text-body-2" data-testid="tx-guidance">{{ t(`tx.guidance.${view.guidance}` as MessageKey) }}</p>
        <p v-if="view.exitCode !== null && view.status === 'failed'" class="text-caption" data-testid="tx-exit-code">{{ t("tx.job.exitCode", { code: view.exitCode }) }}</p>
        <div data-testid="tx-outputs">
          <p class="text-body-2">{{ view.outputCount > 0 ? t("tx.job.outputs", { count: view.outputCount }) : t("tx.job.noOutputs") }}</p>
          <ul v-if="shownOutputs.length > 0" class="text-body-2 u-notes">
            <li v-for="name in shownOutputs" :key="name">{{ name }}</li>
            <li v-if="moreOutputs > 0">{{ t("tx.job.outputsMore", { count: moreOutputs }) }}</li>
          </ul>
          <p v-if="s.snapshot && view.outputCount > 0" class="text-caption u-break" data-testid="tx-output-folder">{{ t("tx.job.outputFolder", { folder: s.snapshot.request.outputDir }) }}</p>
        </div>
        <div class="u-row">
          <v-btn v-if="view.outputCount > 0" variant="outlined" size="small" :loading="opening" data-testid="tx-open-folder" @click="openFolder">{{ t("tx.job.openFolder") }}</v-btn>
          <LegacyLink :label="t('app.openLegacy')" size="small" testid="tx-legacy-history" />
          <v-btn variant="text" size="small" data-testid="tx-dismiss" @click="core.job.dismiss()">{{ t("tx.job.dismiss") }}</v-btn>
        </div>
        <p v-if="openFailed" class="text-caption" role="status" data-testid="tx-open-failed">{{ t("tx.job.openFolderFailed") }}</p>
        <p class="text-caption">{{ t("tx.job.legacyHistory") }}</p>
        <p class="text-caption">{{ t("tx.job.dismissNote") }}</p>
      </template>
      <div v-if="s.tracking === 'lost'" class="u-row">
        <LegacyLink :label="t('app.openLegacy')" size="small" testid="tx-lost-legacy" />
        <v-btn variant="text" size="small" data-testid="tx-dismiss" @click="core.job.dismiss()">{{ t("tx.job.dismiss") }}</v-btn>
      </div>
    </v-card>

    <template v-if="s.snapshot && (view || pending || s.submit.phase === 'unknown')">
      <p class="text-caption" data-testid="tx-snapshot-note">{{ t("tx.job.snapshot") }}</p>
      <p class="text-caption u-break" data-testid="tx-snapshot">{{ snapshotText }}</p>
    </template>
    <p class="text-caption" data-testid="tx-nonpersistent">{{ t("tx.job.nonPersistent") }}</p>
  </section>
</template>
