<script setup lang="ts">
import { useCore } from "../../composables/core";
import { useCodeText } from "../../composables/v1";
import type { JobV1 } from "../../api/v1Resources";
import type { LogicalJob } from "../../domain/v1/logicalJobs";
import { canRerun } from "../../domain/v1/batchJobs";
import V1Artifacts from "../transcribe/V1Artifacts.vue";

const props = defineProps<{ group: LogicalJob; label: string; history: boolean; hidden: boolean; busy: boolean }>();
const { board, deletion, rerun, reexport } = useCore().v1!;
const { t, status, reason } = useCodeText();

function plan(attempt: JobV1): void {
  if (props.busy || deletion.isOpen() || !board.canDelete(props.group)) return;
  void deletion.plan({ rootId: props.group.rootId, jobId: attempt.id, revision: attempt.revision, mode: "history" });
}
</script>

<template>
  <v-card variant="outlined" class="pa-4 u-stack u-break" data-testid="results-detail" :data-root="group.rootId" :data-job="group.latest.id">
    <h2 class="u-subheading">{{ label }}</h2>
    <dl class="summary text-body-2">
      <dt>{{ t("results.root") }}</dt><dd>{{ group.rootId }}</dd>
      <dt>{{ t("results.latest") }}</dt><dd>{{ group.latest.id }}</dd>
      <dt>{{ t("results.status") }}</dt><dd>{{ status(group.latest.status) }}</dd>
      <dt>{{ t("results.reason") }}</dt><dd><code>{{ group.latest.reasonCode }}</code> · {{ reason(group.latest.reasonCode) }}</dd>
      <template v-if="group.latest.errorCode">
        <dt>{{ t("results.error") }}</dt><dd><code>{{ group.latest.errorCode }}</code> · {{ reason(group.latest.errorCode) }}</dd>
      </template>
      <dt>{{ t("results.model") }}</dt><dd>{{ group.latest.spec.modelId }}</dd>
      <dt>{{ t("results.profile") }}</dt><dd>{{ group.latest.spec.profileId }} · {{ group.latest.spec.device }}</dd>
      <dt>{{ t("results.backend") }}</dt><dd>{{ group.latest.spec.backend }} · {{ group.latest.spec.computeType }}</dd>
      <template v-if="group.rerunOfRootId">
        <dt>{{ t("results.rerunOf") }}</dt><dd>{{ group.rerunOfRootId }}</dd>
      </template>
    </dl>
    <div v-if="history" class="u-row">
      <template v-if="!hidden">
        <v-btn v-if="group.latest.status !== 'succeeded'" variant="outlined" size="small" :disabled="busy || !board.canRetry(group)" data-testid="v1-board-retry" @click="board.retry(group.rootId)">
          {{ t("v1.board.retry") }}
        </v-btn>
        <v-btn v-if="board.store.get().visibilitySupported" variant="text" size="small" :disabled="busy || !board.canHide(group)" data-testid="v1-board-hide" @click="board.setHidden(group.rootId, true)">
          {{ t("v1.board.hide") }}
        </v-btn>
      </template>
      <v-btn v-else variant="text" size="small" :disabled="busy" data-testid="v1-board-show" @click="board.setHidden(group.rootId, false)">
        {{ t("v1.board.show") }}
      </v-btn>
    </div>
    <div v-if="canRerun(group)" class="u-row">
      <v-btn size="small" variant="outlined" :disabled="busy" data-testid="result-rerun" @click="rerun.openRerun(group)">{{ t("results.rerun") }}</v-btn>
      <!-- E-C: a finished, succeeded job only; no ASR (the server decides availability). -->
      <v-btn
        v-if="group.latest.status === 'succeeded'" size="small" variant="outlined" :disabled="busy"
        data-testid="result-reexport" @click="reexport.open(group.latest.id)"
      >
        {{ t("ec.open") }}
      </v-btn>
    </div>
    <h3 class="u-subheading">{{ t("results.attempts") }}</h3>
    <p v-if="group.branched" class="text-caption">{{ t("v1.board.branched") }}</p>
    <p v-if="group.incomplete" class="text-caption">{{ t("v1.board.incomplete") }}</p>
    <ul class="u-notes text-body-2">
      <li v-for="attempt in group.attempts" :key="attempt.id" data-testid="results-attempt" :data-job="attempt.id">
        <div>{{ attempt.id }} · {{ status(attempt.status) }}</div>
        <div>{{ t("results.timestamps", { created: attempt.createdAt, updated: attempt.updatedAt }) }}</div>
        <div v-if="attempt.retryOf">{{ t("results.retryOf") }} {{ attempt.retryOf }}</div>
        <div v-if="attempt.rerunOf">{{ t("results.rerunOf") }} {{ attempt.rerunOf }}</div>
        <v-btn
          v-if="history" size="small" variant="text" color="error" :disabled="busy || !board.canDelete(group)"
          :aria-label="t('results.deleteAttempt', { id: attempt.id })" data-testid="v1-board-delete-history" @click="plan(attempt)"
        >
          {{ t("v1.board.deleteHistory") }}
        </v-btn>
      </li>
    </ul>
    <p v-if="history" class="text-caption" data-testid="v1-delete-refusal" data-refusal="artifact_deletion_disabled">
      {{ t("v1.delete.refusal.artifact_deletion_disabled") }}
    </p>
    <v-divider />
    <V1Artifacts :key="group.latest.id" :job-id="group.latest.id" show-external-status />
  </v-card>
</template>

<style scoped>
.summary { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 12px; }
.summary dt { font-weight: 600; }
</style>
