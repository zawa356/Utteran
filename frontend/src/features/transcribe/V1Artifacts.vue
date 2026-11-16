<script setup lang="ts">
import { computed } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import type { MessageKey } from "../../i18n";

/**
 * Read-only artifact view of one internal job (`GET /api/v1/jobs/{id}/artifacts`): format, size and
 * whether the bytes still match the record made at completion. No file name and no path are shown
 * (the API returns none). Read on request only; nothing here writes or deletes.
 */
const props = defineProps<{ jobId: string }>();
const core = useCore();
const board = core.v1!.board;
const state = useStore(board.store);
const { t } = useI18n();
const entry = computed(() => state.value.artifacts[props.jobId] ?? null);
const view = computed(() => entry.value?.view ?? null);
</script>

<template>
  <div class="u-stack" data-testid="v1-artifacts" :data-job="jobId" :data-state="view?.state ?? entry?.phase ?? 'idle'">
    <div class="u-row">
      <v-btn variant="text" size="small" :loading="entry?.phase === 'pending'" data-testid="v1-artifacts-load" @click="board.loadArtifacts(jobId)">{{ t("v1.board.artifacts") }}</v-btn>
    </div>
    <ErrorAlert v-if="entry?.phase === 'error' && entry.error" :error="entry.error" testid="v1-artifacts-error" @retry="board.loadArtifacts(jobId)" />
    <template v-if="view">
      <p class="text-body-2" data-testid="v1-artifacts-state">{{ t(`v1.artifacts.state.${view.state}` as MessageKey) }}</p>
      <ul v-if="view.items.length > 0" class="text-body-2 u-notes" data-testid="v1-artifacts-items">
        <li v-for="item in view.items" :key="item.format" :data-format="item.format" :data-integrity="item.integrity">
          {{ t("v1.artifacts.item", { format: item.format.toUpperCase(), size: item.size, integrity: t(`v1.artifacts.integrity.${item.integrity}` as MessageKey) }) }}
        </li>
      </ul>
      <v-alert v-if="view.unexpectedCount > 0" type="warning" density="compact" data-testid="v1-artifacts-unexpected">{{ t("v1.artifacts.unexpected", { count: view.unexpectedCount }) }}</v-alert>
      <p class="text-caption">{{ t("v1.artifacts.note") }}</p>
    </template>
  </div>
</template>
