<script setup lang="ts">
import { computed } from "vue";
import { fileName } from "../../api/legacyJobs";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { countItems } from "../../domain/batchSubmission";
import { isLocked, OVERRIDE_FIELDS } from "../../domain/inputCollection";
import type { MessageKey } from "../../i18n";
import InputAdd from "./InputAdd.vue";
import RowStatusChip from "./RowStatusChip.vue";

/**
 * Normal mode input: the shared list, shown short. One *current* file (the list's primary row) is
 * started here like S3-A; several files, per-file settings and the staged batch live in Expert.
 */
const { core, list, batch, primary, primaryStatus, statusOf } = useTranscription();
const { t } = useI18n();

const rows = computed(() => list.value.rows);
const choices = computed(() => rows.value.map((r) => ({ title: `${fileName(r.inputPath) ?? "?"} — ${t(`tx.row.status.${statusOf(r.clientFileId)}` as MessageKey)}`, value: r.clientFileId })));
const counts = computed(() => {
  const c = { not_submitted: 0, pending: 0, accepted: 0, rejected: 0, unknown: 0 };
  for (const r of rows.value) c[statusOf(r.clientFileId)] += 1;
  return c;
});
const hasOverrides = computed(() => !!primary.value && (OVERRIDE_FIELDS.some((f) => primary.value!.overrides[f] !== undefined) || primary.value.output.kind === "custom"));
const batchRun = computed(() => batch.value.run);
const batchRunning = computed(() => batchRun.value?.phase === "running" || batchRun.value?.phase === "stopping");
const batchCounts = computed(() => (batchRun.value ? countItems(batchRun.value.items) : null));
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-input-heading" data-testid="tx-input">
    <h2 id="tx-input-heading" class="u-subheading">{{ t("tx.input.title") }}</h2>
    <InputAdd />

    <div v-if="primary" class="u-stack" data-testid="tx-input-primary">
      <v-select
        v-if="rows.length > 1"
        :label="t('tx.input.current')"
        :items="choices"
        :model-value="primary.clientFileId"
        hide-details
        data-testid="tx-input-choose"
        @update:model-value="(v: string) => core.inputs.setPrimary(v)"
      />
      <p class="text-body-2 u-path" data-testid="tx-input-current">{{ primary.inputPath }}</p>
      <div class="u-row">
        <RowStatusChip :status="primaryStatus" testid="tx-input-status" />
        <span class="text-caption" data-testid="tx-input-source">{{ t(`tx.input.source.${primary.source}` as MessageKey) }}</span>
        <v-btn variant="text" size="small" :disabled="isLocked(primaryStatus)" data-testid="tx-input-clear" @click="core.inputs.remove([primary.clientFileId])">
          {{ t("tx.input.clear") }}
        </v-btn>
      </div>
      <p v-if="hasOverrides" class="text-caption" data-testid="tx-input-overrides">{{ t("tx.input.hasOverrides") }}</p>
    </div>
    <p v-else class="text-body-2" data-testid="tx-input-none">{{ t("tx.input.none") }}</p>

    <div v-if="rows.length > 1 || batchRun" class="u-stack" data-testid="tx-input-summary">
      <p class="text-body-2">
        {{ t("tx.input.summary", { total: rows.length, open: counts.not_submitted, accepted: counts.accepted, rejected: counts.rejected, unknown: counts.unknown }) }}
      </p>
      <p v-if="batchRunning && batchCounts" class="text-body-2" role="status" data-testid="tx-input-batch">{{ t("tx.input.batchRunning", { accepted: batchCounts.accepted, total: batchRun!.items.length }) }}</p>
      <div class="u-row">
        <v-btn variant="text" size="small" data-testid="tx-input-to-expert" @click="core.shell.setMode('expert')">{{ t("tx.input.toExpert") }}</v-btn>
      </div>
    </div>
  </section>
</template>
