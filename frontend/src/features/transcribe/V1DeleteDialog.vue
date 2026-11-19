<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { JobV1 } from "../../api/v1Resources";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { MessageKey } from "../../i18n";

/**
 * Bundle C2c confirmation (`--next-ui-v1`). One dialog per mode, opened from one of two separate row
 * buttons ("delete history" / "delete history and outputs"). The target is one internal job id, shown
 * verbatim; a row with several attempts asks which one. Default focus is the cancel button. The
 * dialog never says "completely erased": it lists what the server says is retained.
 */
const props = defineProps<{ candidates: readonly JobV1[] }>();
const core = useCore();
const deletion = core.v1!.deletion;
const s = useStore(deletion.store);
const queueGate = useStore(deletion.gate.store);
const { t, status, reason } = useCodeText();
const cancelButton = ref<{ $el: HTMLElement } | null>(null);

const open = computed(() => s.value.phase !== "idle");
const mode = computed(() => s.value.target?.mode ?? "history");
const executing = computed(() => s.value.phase === "executing");
const settled = computed(() => ["done", "incomplete", "rejected", "conflict"].includes(s.value.phase));
const title = computed(() => t(mode.value === "history" ? "v1.delete.history.title" : "v1.delete.artifacts.title"));
const body = computed(() => t(mode.value === "history" ? "v1.delete.history.body" : "v1.delete.artifacts.body"));

function choose(jobId: string | null): void {
  const target = s.value.target;
  const job = props.candidates.find((item) => item.id === jobId);
  if (!target || !job || job.id === target.jobId || deletion.busy()) return;
  void deletion.plan({ ...target, jobId: job.id, revision: job.revision });
}

const canConfirm = computed(() => {
  void s.value;
  void queueGate.value;
  return deletion.canConfirm();
});

function close(): void {
  // After an unknown outcome the lists are read again (what the server has is the truth).
  const unknown = s.value.phase === "unknown";
  deletion.close();
  if (unknown) void core.v1!.board.refresh();
}

// The safe choice has the focus whenever a plan is shown.
watch(
  () => s.value.phase,
  async (phase) => {
    if (phase === "planned" || phase === "plan_error") {
      await nextTick();
      cancelButton.value?.$el?.focus();
    }
  },
);
</script>

<template>
  <v-dialog :model-value="open" max-width="560" persistent data-testid="v1-delete-dialog" @update:model-value="(v: boolean) => (!v ? close() : undefined)">
    <!-- The app default card variant is "outlined" (transparent): a dialog needs its own surface. -->
    <v-card v-if="s.target" variant="flat" color="surface" elevation="8" :title="title" data-testid="v1-delete-card" :data-mode="s.target.mode" :data-phase="s.phase" :data-job="s.target.jobId">
      <v-card-text class="u-stack">
        <p class="text-body-2" data-testid="v1-delete-body">{{ body }}</p>
        <p class="text-body-2 u-break" data-testid="v1-delete-target">{{ t("v1.delete.target", { id: s.target.jobId }) }}</p>
        <template v-if="candidates.length > 1">
          <p class="text-caption">{{ t("v1.delete.chooseTarget") }}</p>
          <v-radio-group :model-value="s.target.jobId" density="compact" hide-details :disabled="executing || s.phase === 'unknown' || settled" data-testid="v1-delete-choose" @update:model-value="choose">
            <v-radio v-for="job in candidates" :key="job.id" :value="job.id" :label="t('v1.delete.targetOption', { status: status(job.status), id: job.id })" :data-job="job.id" data-testid="v1-delete-option" />
          </v-radio-group>
        </template>

        <p v-if="s.phase === 'planning'" role="status" class="text-body-2" data-testid="v1-delete-planning">{{ t("v1.delete.planning") }}</p>
        <ErrorAlert v-if="s.phase === 'plan_error' && s.error" :error="s.error" testid="v1-delete-plan-error" @retry="deletion.plan(s.target)" />

        <template v-if="s.plan && !settled && s.phase !== 'unknown'">
          <v-alert v-if="!s.plan.allowed && s.plan.refusal" type="warning" density="compact" data-testid="v1-delete-refusal" :data-refusal="s.plan.refusal">
            {{ t("v1.delete.refused", { reason: t(`v1.delete.refusal.${s.plan.refusal}` as MessageKey) }) }}
          </v-alert>
          <template v-if="s.plan.allowed">
            <p v-if="s.plan.resume" class="text-body-2" data-testid="v1-delete-resume">{{ t("v1.delete.resume") }}</p>
            <template v-if="s.plan.mode === 'history_and_artifacts'">
              <template v-if="s.plan.artifacts.length > 0">
                <p class="text-body-2">{{ t("v1.delete.files", { count: s.plan.artifacts.length }) }}</p>
                <ul class="text-body-2 u-notes" data-testid="v1-delete-files">
                  <li v-for="item in s.plan.artifacts" :key="item.format" :data-format="item.format">{{ t("v1.delete.file", { format: item.format.toUpperCase(), size: item.size }) }}</li>
                </ul>
              </template>
              <p v-else class="text-body-2" data-testid="v1-delete-no-files">{{ t("v1.delete.noFiles") }}</p>
            </template>
          </template>
          <!-- What remains is described only for a deletion that can happen (a refused plan deletes nothing). -->
          <template v-if="s.plan.allowed">
            <p v-if="s.plan.relatedJobs > 0" class="text-body-2" data-testid="v1-delete-related">{{ t("v1.delete.related", { count: s.plan.relatedJobs }) }}</p>
            <p class="text-caption">{{ t("v1.delete.retainedTitle") }}</p>
            <ul class="text-caption u-notes" data-testid="v1-delete-retained">
              <li v-for="code in s.plan.retained" :key="code" :data-code="code">{{ t(`v1.delete.retained.${code}` as MessageKey) }}</li>
            </ul>
          </template>
        </template>

        <p v-if="executing" role="status" class="text-body-2" data-testid="v1-delete-executing">{{ t("v1.delete.executing") }}</p>
        <v-alert v-if="s.phase === 'done' && s.result" type="success" density="compact" role="status" data-testid="v1-delete-outcome" data-outcome="done">
          {{ s.result.mode === "history" ? t("v1.delete.done.history") : t("v1.delete.done.artifacts", { count: s.result.artifactsRemoved }) }}
        </v-alert>
        <v-alert v-if="s.phase === 'incomplete' && s.result" type="warning" density="compact" role="status" data-testid="v1-delete-outcome" data-outcome="incomplete">
          {{ t("v1.delete.incomplete", { removed: s.result.artifactsRemoved, remaining: s.result.artifactsRemaining }) }}
        </v-alert>
        <v-alert v-if="s.phase === 'rejected' || s.phase === 'conflict' || s.phase === 'unknown'" :type="s.phase === 'unknown' ? 'warning' : 'error'" density="compact" role="status" data-testid="v1-delete-outcome" :data-outcome="s.phase">
          {{ t(`v1.delete.${s.phase}` as MessageKey) }}
          <span v-if="s.error?.code" class="text-caption"> ({{ reason(s.error.code) }})</span>
        </v-alert>
        <template v-if="settled && s.result">
          <p class="text-caption">{{ t("v1.delete.retainedTitle") }}</p>
          <ul class="text-caption u-notes" data-testid="v1-delete-result-retained">
            <li v-for="code in s.result.retained" :key="code" :data-code="code">{{ t(`v1.delete.retained.${code}` as MessageKey) }}</li>
          </ul>
        </template>
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-btn ref="cancelButton" variant="flat" color="primary" :disabled="executing" data-testid="v1-delete-cancel" @click="close">
          {{ settled || s.phase === "unknown" ? t("v1.delete.close") : t("v1.delete.cancel") }}
        </v-btn>
        <v-btn v-if="s.phase === 'unknown'" variant="outlined" data-testid="v1-delete-recheck" @click="deletion.recheck()">{{ t("v1.delete.recheck") }}</v-btn>
        <v-btn v-if="!settled && s.phase !== 'unknown'" variant="outlined" color="error" :disabled="!canConfirm || executing" :loading="executing" data-testid="v1-delete-confirm" @click="deletion.confirm()">
          {{ t("v1.delete.confirm") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
