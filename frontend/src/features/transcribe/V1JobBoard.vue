<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { DeletionModeV1, JobV1 } from "../../api/v1Resources";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { LogicalJob } from "../../domain/v1/logicalJobs";
import type { MessageKey } from "../../i18n";
import V1Artifacts from "./V1Artifacts.vue";
import V1DeleteDialog from "./V1DeleteDialog.vue";

/**
 * Bundle C2b (`--next-ui-v1`): every durable job as one row per logical job (a `retry_of` chain,
 * Design Freeze §7). Queue = not finished, history = finished, hidden history on request.
 * "Hide" never deletes anything. The lists are always what the server answered last.
 */
const core = useCore();
const board = core.v1!.board;
const job = core.v1!.job;
const s = useStore(board.store);
const inputs = useStore(core.inputs.store);
const { t, status, reason } = useCodeText();

onMounted(() => board.watch(true));
onUnmounted(() => board.watch(false));

// File names are known only for rows of this page (the API carries no path).
const names = computed(() => new Map(inputs.value.rows.map((row) => [job.fileIdFor(row.clientFileId), fileName(row.inputPath) ?? "?"])));
const label = (g: LogicalJob) => names.value.get(g.latest.clientFileId) ?? t("v1.board.unknownFile");
// The job a row shows: for an unfinished row the unfinished job B7 runs first, never just "the newest".
const shown = (g: LogicalJob) => {
  void s.value;
  return board.current(g);
};
/** B7's own place for the shown job; without one the row claims no place in the order. */
function place(g: LogicalJob): string {
  const p = board.placeOf(shown(g).id);
  if (p === null) return t("v1.board.place.unknown");
  if (p.active) return t("v1.board.place.active");
  const base = p.position === null ? t("v1.board.place.unknown") : t("v1.board.place.waiting", { position: p.position });
  return p.held ? `${base} · ${t("v1.board.place.held")}` : base;
}
const deletion = core.v1!.deletion;
const d = useStore(deletion.store);
const open = computed(() => {
  void s.value;
  void d.value;
  return board.actionOpen() || deletion.isOpen();
});
// C2c: the internal jobs of the row the deletion dialog was opened from (the person picks one).
const candidates = ref<readonly JobV1[]>([]);
function openDeletion(g: LogicalJob, mode: DeletionModeV1): void {
  if (!board.canDelete(g) || deletion.isOpen()) return;
  const target = shown(g);
  candidates.value = [...g.attempts];
  void deletion.plan({ rootId: g.rootId, jobId: target.id, revision: target.revision, mode });
}
const sections = computed(() => [
  { key: "queue" as const, title: t("v1.board.queue"), empty: t("v1.board.emptyQueue"), rows: s.value.queue, show: true },
  { key: "history" as const, title: t("v1.board.history"), empty: t("v1.board.emptyHistory"), rows: s.value.history, show: true },
  { key: "hidden" as const, title: t("v1.board.hidden"), empty: t("v1.board.emptyHidden"), rows: s.value.hidden, show: s.value.showHidden && s.value.visibilitySupported },
]);
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-board-heading" data-testid="v1-board" :data-phase="s.phase">
    <h2 id="v1-board-heading" class="u-subheading">{{ t("v1.board.title") }}</h2>
    <p class="text-caption">{{ t("v1.board.explain") }}</p>
    <ErrorAlert v-if="s.phase === 'error' && s.error" :error="s.error" testid="v1-board-error" @retry="board.refresh()" />

    <v-alert
      v-if="s.action"
      :type="s.action.phase === 'done' ? 'success' : s.action.phase === 'pending' ? 'info' : 'warning'"
      density="compact"
      role="status"
      data-testid="v1-board-action"
      :data-kind="s.action.kind"
      :data-action-phase="s.action.phase"
    >
      {{ t(`v1.board.action.${s.action.phase}` as MessageKey) }}
      <span v-if="s.action.error?.code" class="text-caption"> ({{ reason(s.action.error.code) }})</span>
      <template #append>
        <v-btn v-if="s.action.phase === 'unknown'" variant="text" size="small" data-testid="v1-board-recheck" @click="board.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
        <v-btn v-if="s.action.phase !== 'pending'" variant="text" size="small" data-testid="v1-board-action-close" @click="board.dismissAction()">{{ t("v1.board.action.close") }}</v-btn>
      </template>
    </v-alert>

    <div class="u-row">
      <v-btn variant="text" size="small" :loading="s.phase === 'pending'" data-testid="v1-board-refresh" @click="board.refresh()">{{ t("v1.board.refresh") }}</v-btn>
      <v-switch
        v-if="s.visibilitySupported"
        :model-value="s.showHidden"
        :label="t('v1.board.showHidden')"
        density="compact"
        hide-details
        color="primary"
        data-testid="v1-board-show-hidden"
        @update:model-value="(v: boolean | null) => board.setShowHidden(v === true)"
      />
      <span v-else class="text-caption" data-testid="v1-board-no-visibility">{{ t("v1.board.noVisibility") }}</span>
    </div>

    <template v-for="section in sections" :key="section.key">
      <v-card v-if="section.show" variant="outlined" class="pa-3 u-stack" :data-testid="`v1-board-${section.key}`" :data-count="section.rows.length">
        <h3 class="u-subheading">{{ section.title }}（{{ section.rows.length }}）</h3>
        <p v-if="section.key === 'queue' && section.rows.length > 0" class="text-caption">{{ t("v1.board.queueOrderNote") }}</p>
        <p v-if="s.phase === 'done' && section.rows.length === 0" class="text-body-2">{{ section.empty }}</p>
        <v-list v-if="section.rows.length > 0" density="compact" lines="three">
          <v-list-item
            v-for="g in section.rows"
            :key="g.rootId"
            data-testid="v1-board-row"
            :data-root="g.rootId"
            :data-job="shown(g).id"
            :data-status="shown(g).status"
            :data-attempts="g.attempts.length"
            :data-open="g.open.length"
          >
            <v-list-item-title class="u-break" data-testid="v1-board-row-title">{{ t("v1.board.row", { status: status(shown(g).status), label: label(g) }) }}</v-list-item-title>
            <v-list-item-subtitle class="u-break">
              {{ t("v1.board.rowMeta", { attempts: g.attempts.length, model: shown(g).spec.modelId, profile: shown(g).spec.profileId, created: g.attempts[0]!.createdAt }) }}
            </v-list-item-subtitle>
            <div v-if="section.key === 'queue'" class="text-caption" data-testid="v1-board-row-place">{{ place(g) }}</div>
            <div class="text-caption u-break">{{ t("v1.board.rowJob", { id: shown(g).id }) }}</div>
            <div v-if="shown(g).errorCode" class="text-caption" data-testid="v1-board-row-error">{{ reason(shown(g).errorCode!) }}</div>
            <div v-if="shown(g).blockReason" class="text-caption" data-testid="v1-board-row-block">{{ reason(shown(g).blockReason!) }}</div>
            <!-- A branched chain: every unfinished internal job is listed and cancelled by its own id. -->
            <v-alert v-if="g.open.length > 1" type="warning" density="compact" class="mt-1" data-testid="v1-board-row-branches">
              {{ t("v1.board.branches", { count: g.open.length }) }}
              <ul class="u-notes">
                <li v-for="branch in g.open" :key="branch.id" class="u-break" data-testid="v1-board-branch" :data-job="branch.id" :data-status="branch.status">
                  {{ status(branch.status) }} · {{ branch.id }}
                  <v-btn variant="text" color="error" size="small" :disabled="!board.canCancel(g, branch.id) || open" data-testid="v1-board-branch-cancel" @click="board.cancel(g.rootId, branch.id)">{{ t("v1.board.cancel") }}</v-btn>
                </li>
              </ul>
            </v-alert>
            <div v-else-if="g.branched" class="text-caption" data-testid="v1-board-row-branched">{{ t("v1.board.branched") }}</div>
            <div v-if="g.rerunOfRootId" class="text-caption">{{ t("v1.board.rerunOf", { id: g.rerunOfRootId }) }}</div>
            <div v-if="g.incomplete" class="text-caption" data-testid="v1-board-row-incomplete">{{ t("v1.board.incomplete") }}</div>
            <div class="u-row mt-1">
              <v-btn v-if="section.key === 'queue'" variant="outlined" color="error" size="small" :disabled="!board.canCancel(g) || open" data-testid="v1-board-cancel" @click="board.cancel(g.rootId)">{{ t("v1.board.cancel") }}</v-btn>
              <v-btn v-if="section.key === 'history'" variant="outlined" size="small" :disabled="!board.canRetry(g) || open" data-testid="v1-board-retry" @click="board.retry(g.rootId)">{{ t("v1.board.retry") }}</v-btn>
              <v-btn v-if="section.key === 'history' && s.visibilitySupported" variant="text" size="small" :disabled="!board.canHide(g) || open" data-testid="v1-board-hide" @click="board.setHidden(g.rootId, true)">{{ t("v1.board.hide") }}</v-btn>
              <v-btn v-if="section.key === 'hidden'" variant="text" size="small" :disabled="open" data-testid="v1-board-show" @click="board.setHidden(g.rootId, false)">{{ t("v1.board.show") }}</v-btn>
              <template v-if="section.key !== 'queue'">
                <v-btn variant="text" color="error" size="small" :disabled="!board.canDelete(g) || open" data-testid="v1-board-delete-history" @click="openDeletion(g, 'history')">{{ t("v1.board.deleteHistory") }}</v-btn>
                <v-btn variant="text" color="error" size="small" :disabled="!board.canDelete(g) || open" data-testid="v1-board-delete-artifacts" @click="openDeletion(g, 'history_and_artifacts')">{{ t("v1.board.deleteArtifacts") }}</v-btn>
              </template>
            </div>
            <V1Artifacts v-if="section.key !== 'queue'" :job-id="shown(g).id" />
          </v-list-item>
        </v-list>
      </v-card>
    </template>
    <V1DeleteDialog :candidates="candidates" />
  </section>
</template>
