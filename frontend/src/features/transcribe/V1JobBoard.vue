<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted } from "vue";
import { fileName } from "../../api/legacyJobs";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { LogicalJob } from "../../domain/v1/logicalJobs";
import type { MessageKey } from "../../i18n";

/** Compact queue summary for Expert transcription. */
const core = useCore();
const board = core.v1!.board;
const job = core.v1!.job;
const s = useStore(board.store);
const inputs = useStore(core.inputs.store);
const summary = computed(() => s.value.queue.slice(0, 5));
const { t, status, reason } = useCodeText();

// Share the board timer with the queue and results pages.
let release: (() => void) | null = null;
function retain() {
  release ??= board.retainWatch();
}
function stop() {
  release?.();
  release = null;
}
onMounted(retain);
onActivated(retain);
onDeactivated(stop);
onUnmounted(stop);

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
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-board-heading" data-testid="v1-board" :data-phase="s.phase">
    <h2 id="v1-board-heading" class="u-subheading">{{ t("v1.board.title") }}</h2>
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
        <v-btn v-if="s.action.phase !== 'pending' && s.action.phase !== 'unknown'" variant="text" size="small" data-testid="v1-board-action-close" @click="board.dismissAction()">{{ t("v1.board.action.close") }}</v-btn>
      </template>
    </v-alert>

    <div class="u-row">
      <v-btn variant="text" size="small" :loading="s.phase === 'pending'" data-testid="v1-board-refresh" @click="board.refresh()">{{ t("v1.board.refresh") }}</v-btn>
      <v-btn variant="text" size="small" data-testid="v1-board-open-queue" @click="core.shell.navigate('queue')">{{ t("section.queue") }}</v-btn>
      <v-btn variant="text" size="small" data-testid="v1-board-open-results" @click="core.shell.navigate('results')">{{ t("results.open") }}</v-btn>
    </div>

    <div data-testid="v1-board-queue" :data-count="s.queue.length">
      <p v-if="summary.length < s.queue.length" class="text-caption">{{ t("v1.board.summaryCount", { shown: summary.length, total: s.queue.length }) }}</p>
      <p v-if="s.phase === 'idle' || s.phase === 'pending'" role="status" class="text-body-2">{{ t("queue.loading") }}</p>
      <p v-if="s.phase === 'done' && s.queue.length === 0" class="text-body-2">{{ t("v1.board.emptyQueue") }}</p>
      <v-list v-if="s.queue.length > 0" density="compact" lines="three">
        <v-list-item
          v-for="g in summary"
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
          <div class="text-caption" data-testid="v1-board-row-place">{{ place(g) }}</div>
          <div class="text-caption u-break">{{ t("v1.board.rowJob", { id: shown(g).id }) }}</div>
          <div v-if="shown(g).errorCode" class="text-caption" data-testid="v1-board-row-error">{{ reason(shown(g).errorCode!) }}</div>
          <div v-if="shown(g).blockReason" class="text-caption" data-testid="v1-board-row-block">{{ reason(shown(g).blockReason!) }}</div>
          <!-- A branched chain: every unfinished internal job is listed and cancelled by its own id. -->
          <v-alert v-if="g.open.length > 1" type="warning" density="compact" class="mt-1" data-testid="v1-board-row-branches">
            {{ t("v1.board.branches", { count: g.open.length }) }}
            <ul class="u-notes">
              <li v-for="branch in g.open" :key="branch.id" class="u-break" data-testid="v1-board-branch" :data-job="branch.id" :data-status="branch.status">
                {{ status(branch.status) }} · {{ branch.id }}
              </li>
            </ul>
          </v-alert>
          <div v-else-if="g.branched" class="text-caption" data-testid="v1-board-row-branched">{{ t("v1.board.branched") }}</div>
          <div v-if="g.rerunOfRootId" class="text-caption">{{ t("v1.board.rerunOf", { id: g.rerunOfRootId }) }}</div>
          <div v-if="g.incomplete" class="text-caption" data-testid="v1-board-row-incomplete">{{ t("v1.board.incomplete") }}</div>
        </v-list-item>
      </v-list>
    </div>
  </section>
</template>
