<script setup lang="ts">
import { computed, ref } from "vue";
import { fileName } from "../../api/legacyJobs";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { isLocked, OVERRIDE_FIELDS } from "../../domain/inputCollection";
import type { MessageKey } from "../../i18n";
import InputAdd from "./InputAdd.vue";
import RowStatusChip from "./RowStatusChip.vue";

/**
 * Expert ①: the shared input list with a multi-selection. "Remove" only takes rows off this
 * list (display) — no server job is cancelled and no output or history is deleted; rows that are
 * being sent, were accepted or are unknown cannot be removed.
 */
const { core, list, form, env, statusOf } = useTranscription();
const { t } = useI18n();

const rows = computed(() => list.value.rows);
const selectedCount = computed(() => rows.value.filter((r) => r.selected).length);
const removable = computed(() => rows.value.filter((r) => r.selected && !isLocked(statusOf(r.clientFileId))).length);
const confirming = ref(false);
// Rows of a running batch: frozen and still going to be sent, so they are not removable here.
const queuedSelected = computed(() => rows.value.filter((r) => r.selected && statusOf(r.clientFileId) === "queued").length);

function overridden(id: string): boolean {
  const row = rows.value.find((r) => r.clientFileId === id);
  return !!row && (OVERRIDE_FIELDS.some((f) => row.overrides[f] !== undefined) || row.output.kind === "custom");
}
function hasProblem(id: string): boolean {
  const row = rows.value.find((r) => r.clientFileId === id);
  // `form`/`env` are read so this follows settings and environment changes.
  if (!row || !form.value || !env.value || statusOf(id) !== "not_submitted") return false;
  return core.inputs.evaluateRow(row).issues.length > 0;
}
function removeSelected() {
  core.inputs.removeSelected();
  confirming.value = false;
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-list-heading" data-testid="tx-list">
    <h2 id="tx-list-heading" class="u-subheading">{{ t("tx.list.title") }}</h2>
    <InputAdd />
    <div class="u-row">
      <span class="text-body-2" data-testid="tx-list-count">{{ t("tx.list.count", { total: rows.length, selected: selectedCount }) }}</span>
      <v-btn variant="text" size="small" :disabled="rows.length === 0" data-testid="tx-list-select-all" @click="core.inputs.selectAll()">{{ t("tx.list.selectAll") }}</v-btn>
      <v-btn variant="text" size="small" :disabled="selectedCount === 0" data-testid="tx-list-select-none" @click="core.inputs.clearSelection()">{{ t("tx.list.selectNone") }}</v-btn>
      <v-btn variant="text" size="small" color="error" :disabled="removable === 0" data-testid="tx-list-remove" @click="confirming = true">{{ t("tx.list.remove", { count: removable }) }}</v-btn>
    </div>
    <p v-if="queuedSelected > 0" class="text-caption" role="status" data-testid="tx-list-queued-note">{{ t("tx.list.queuedNote", { count: queuedSelected }) }}</p>
    <v-alert v-if="confirming" type="warning" density="compact" data-testid="tx-list-remove-confirm">
      {{ t("tx.list.removeConfirm", { count: removable }) }}
      <div class="u-row mt-2">
        <v-btn size="small" color="error" variant="tonal" data-testid="tx-list-remove-yes" @click="removeSelected">{{ t("tx.list.removeYes") }}</v-btn>
        <v-btn size="small" variant="text" data-testid="tx-list-remove-no" @click="confirming = false">{{ t("common.cancel") }}</v-btn>
      </div>
    </v-alert>
    <p v-if="rows.length === 0" class="text-body-2" data-testid="tx-list-empty">{{ t("tx.input.none") }}</p>
    <v-list v-else density="compact" class="u-input-list" data-testid="tx-list-rows">
      <v-list-item
        v-for="row in rows"
        :key="row.clientFileId"
        :active="row.clientFileId === list.primaryId"
        data-testid="tx-list-row"
        :data-name="fileName(row.inputPath) ?? ''"
        @click="core.inputs.setPrimary(row.clientFileId)"
      >
        <template #prepend>
          <v-checkbox-btn
            :model-value="row.selected"
            :aria-label="t('tx.list.selectRow', { name: fileName(row.inputPath) ?? '?' })"
            data-testid="tx-list-check"
            @click.stop
            @update:model-value="(on: boolean | null) => core.inputs.toggle(row.clientFileId, on === true)"
          />
        </template>
        <v-list-item-title class="u-break">{{ fileName(row.inputPath) ?? "?" }}</v-list-item-title>
        <v-list-item-subtitle class="u-path text-caption">{{ row.inputPath }}</v-list-item-subtitle>
        <div class="u-row mt-1">
          <RowStatusChip :status="statusOf(row.clientFileId)" />
          <span class="text-caption">{{ t(`tx.input.source.${row.source}` as MessageKey) }}</span>
          <v-chip v-if="overridden(row.clientFileId)" size="small" variant="outlined" data-testid="tx-list-overridden">{{ t("tx.list.overridden") }}</v-chip>
          <v-chip v-if="hasProblem(row.clientFileId)" size="small" color="warning" variant="outlined" data-testid="tx-list-problem">{{ t("tx.list.problem") }}</v-chip>
          <span v-if="core.inputs.recordOf(row)?.jobId" class="text-caption" data-testid="tx-list-job-id">{{ t("tx.job.id", { id: core.inputs.recordOf(row)!.jobId! }) }}</span>
          <v-btn
            v-if="statusOf(row.clientFileId) === 'rejected'"
            variant="text"
            size="x-small"
            data-testid="tx-list-reset"
            @click.stop="core.inputs.resetRefusal(row.clientFileId)"
          >
            {{ t("tx.list.resetRefusal") }}
          </v-btn>
        </div>
      </v-list-item>
    </v-list>
    <p class="text-caption">{{ t("tx.list.removeNote") }}</p>
    <p class="text-caption">{{ t("tx.list.duplicateNote") }}</p>
  </section>
</template>
