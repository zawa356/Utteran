<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import QueueControls from "./QueueControls.vue";
import { isHeld, type QueueViewOptions, placeOfRow, type QueueRow } from "./queueRows";

/**
 * Bundle D2-B: detail of the selected logical job. Every value comes from the board's
 * latest answer (the row object is rebuilt on each read, never kept). Only allowlisted DTO fields:
 * ids, state, B7's queue facts, model/profile, times and fixed codes. No path, prompt, token or
 * transcript exists in these DTOs. Controls delegate to the shared board.
 */
const props = defineProps<{ row: QueueRow | null; lost: boolean; filtered: boolean; display: QueueViewOptions }>();
const board = useCore().v1!.board;
const s = useStore(board.store);
const { t, status, reason } = useCodeText();

const placeText = computed(() => {
  if (!props.row) return "";
  const place = placeOfRow(props.row);
  if (place.kind === "active") return t("queue.detail.placeActive");
  if (place.kind === "waiting") return t("queue.detail.placeWaiting", { position: place.position! });
  return t("queue.detail.placeUnknown");
});
/** B7's place of one internal job (a branch or an attempt), as the same short text as the table. */
function placeOf(jobId: string): string | null {
  void s.value;
  const place = board.placeOf(jobId);
  if (place === null) return null;
  if (place.active) return t("queue.place.active");
  return place.position === null ? t("queue.place.unknown") : t("queue.place.waiting", { position: place.position });
}
const targetId = ref<string | undefined>();
watch(() => props.row?.rootId, () => { targetId.value = undefined; });
const target = computed(() => {
  const open = props.row?.group.open ?? [];
  return open.length === 1 ? open[0]!.id : open.some(job => job.id === targetId.value) ? targetId.value : undefined;
});
const branches = computed(() => props.row?.group.open.map(job => ({ value: job.id, title: `${status(job.status)} · ${placeOf(job.id) ?? t("queue.place.unknown")} · ${job.id}` })) ?? []);
const spec = computed(() => props.row?.current.spec ?? null);
</script>

<template>
  <v-card variant="outlined" class="pa-3 u-stack u-queue-detail-card" aria-labelledby="queue-detail-heading" role="region">
    <h2 id="queue-detail-heading" class="u-subheading">{{ t("queue.detail.title") }}</h2>
    <template v-if="!row">
      <v-alert v-if="lost" type="info" density="compact" role="status" data-testid="queue-detail-lost">{{ t("queue.detail.lost") }}</v-alert>
      <p v-else class="text-body-2" data-testid="queue-detail-none">{{ t("queue.detail.none") }}</p>
    </template>
    <div v-else class="u-stack" data-testid="queue-detail" :data-root="row.rootId" :data-job="row.current.id" :data-status="row.current.status">
      <p v-if="filtered" class="text-caption" data-testid="queue-detail-filtered">{{ t("queue.detail.filtered") }}</p>
      <dl class="u-queue-dl">
        <dt>{{ t("queue.detail.logical") }}</dt>
        <dd class="u-break" data-testid="queue-detail-logical">{{ row.rootId }}</dd>
        <dt>{{ t("queue.detail.current") }}</dt>
        <dd class="u-break" data-testid="queue-detail-current">{{ row.current.id }}</dd>
        <dt>{{ t("queue.detail.status") }}</dt>
        <dd>{{ status(row.current.status) }}</dd>
        <dt>{{ t("queue.detail.place") }}</dt>
        <dd data-testid="queue-detail-place">
          {{ placeText }}<template v-if="isHeld(row)"> · {{ t("queue.detail.held") }}</template>
        </dd>
        <dt>{{ t("queue.detail.model") }}</dt>
        <dd class="u-break">{{ spec!.modelId }} ({{ spec!.modelRevision }})</dd>
        <dt>{{ t("queue.detail.execution") }}</dt>
        <dd class="u-break">{{ spec!.profileId }} · {{ spec!.backend }} · {{ spec!.device }} · {{ spec!.computeType }}</dd>
        <dt>{{ t("queue.detail.created") }}</dt>
        <dd>{{ row.group.attempts[0]!.createdAt }}</dd>
        <dt>{{ t("queue.detail.updated") }}</dt>
        <dd>{{ row.current.updatedAt }}</dd>
        <dt>{{ t("queue.detail.reason") }}</dt>
        <dd class="u-break">{{ reason(row.current.reasonCode) }}</dd>
        <template v-if="row.current.errorCode">
          <dt>{{ t("queue.detail.error") }}</dt>
          <dd class="u-break" data-testid="queue-detail-error">{{ reason(row.current.errorCode) }}</dd>
        </template>
        <template v-if="row.current.blockReason">
          <dt>{{ t("queue.detail.block") }}</dt>
          <dd class="u-break" data-testid="queue-detail-block">{{ reason(row.current.blockReason) }}</dd>
        </template>
        <template v-if="row.current.blockers.length > 0">
          <dt>{{ t("queue.detail.blockers") }}</dt>
          <dd class="u-break">{{ row.current.blockers.map(reason).join(" / ") }}</dd>
        </template>
        <template v-if="row.current.recoveryRequired">
          <dt>{{ t("queue.detail.recovery") }}</dt>
          <dd class="u-break" data-testid="queue-detail-recovery">{{ reason(row.current.recoveryRequired) }}</dd>
        </template>
      </dl>
      <v-select v-if="row.group.open.length > 1" v-model="targetId" :items="branches" :label="t('queue.chooseBranch')" density="compact" hide-details data-testid="queue-target" />
      <QueueControls :root-id="row.rootId" :job-id="target" :display="display" />
      <p class="text-caption">{{ t("queue.detail.runs", { count: row.current.attempts }) }}</p>

      <v-alert v-if="row.group.open.length > 1" type="warning" density="compact" data-testid="queue-detail-branches">
        {{ t("queue.detail.branches", { count: row.group.open.length }) }}
        <ul class="u-notes">
          <li v-for="branch in row.group.open" :key="branch.id" class="u-break" data-testid="queue-detail-branch" :data-job="branch.id" :data-status="branch.status">
            {{ status(branch.status) }} · {{ placeOf(branch.id) ?? t("queue.place.unknown") }} · {{ branch.id }}
            <template v-if="branch.id === row.current.id"> ({{ t("queue.detail.currentMark") }})</template>
          </li>
        </ul>
      </v-alert>
      <p v-else-if="row.group.branched" class="text-caption">{{ t("queue.detail.branched") }}</p>
      <p v-if="row.group.incomplete" class="text-caption" data-testid="queue-detail-incomplete">{{ t("v1.board.incomplete") }}</p>
      <p v-if="row.group.rerunOfRootId" class="text-caption u-break">{{ t("v1.board.rerunOf", { id: row.group.rerunOfRootId }) }}</p>

      <div>
        <h3 class="text-body-2 font-weight-medium">{{ t("queue.detail.attempts", { count: row.group.attempts.length }) }}</h3>
        <ol class="u-notes text-caption">
          <li v-for="attempt in row.group.attempts" :key="attempt.id" class="u-break" data-testid="queue-detail-attempt" :data-job="attempt.id" :data-status="attempt.status">
            {{ status(attempt.status) }} · {{ attempt.id }}
            <template v-if="attempt.errorCode"> · {{ reason(attempt.errorCode) }}</template>
            <template v-if="attempt.id === row.current.id"> ({{ t("queue.detail.currentMark") }})</template>
          </li>
        </ol>
      </div>
    </div>
  </v-card>
</template>
