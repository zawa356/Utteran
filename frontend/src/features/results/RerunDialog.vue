<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";

const core = useCore();
const { rerun, library, queueGate } = core.v1!;
const state = useStore(rerun.store);
const target = useStore(rerun.rerunSelection);
const settings = useStore(library.store);
const inputs = useStore(core.inputs.store);
const form = useStore(core.transcription.store);
const gate = useStore(queueGate.store);
const { t, reason, issue } = useCodeText();
const picking = ref(false);
const pickerError = ref(false);
const cancelButton = ref<{ $el: HTMLElement } | null>(null);
let returnFocus: HTMLElement | null = null;

const evaluation = computed(() => {
  void [target.value, settings.value, inputs.value, form.value];
  return rerun.evaluate();
});
const hasSourceRow = computed(() => inputs.value.rows.some(row => row.clientFileId === target.value?.sourceRowId));
const busy = computed(() => {
  void state.value;
  return rerun.busy();
});
const unknown = computed(() => state.value.step === "unknown");
const sourceGone = computed(() => state.value.failedAt === "submission" && [404, 410, 412].includes(state.value.error?.status ?? 0));
const expired = ref(false);
watch(() => state.value.step === "resolved", (resolved, _previous, cleanup) => {
  expired.value = resolved && rerun.isExpired();
  if (!resolved || expired.value) return;
  const timer = setInterval(() => {
    if (rerun.isExpired()) {
      expired.value = true;
      clearInterval(timer);
    }
  }, 1000);
  cleanup(() => clearInterval(timer));
});
const canPrepare = computed(() => {
  void [evaluation.value, state.value];
  return rerun.canPrepare();
});

watch(() => target.value !== null, (open) => {
  if (!open) return;
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  pickerError.value = false;
}, { flush: "sync" });

function focusCancel(): void {
  cancelButton.value?.$el.focus({ preventScroll: true });
}

async function pick(): Promise<void> {
  const selection = target.value;
  if (!selection || picking.value || busy.value || unknown.value || sourceGone.value) return;
  picking.value = true;
  pickerError.value = false;
  try {
    const path = await core.host.pickPath("input_file");
    if (path && target.value === selection) rerun.selectRerunInput(path);
  } catch {
    pickerError.value = true;
  } finally {
    picking.value = false;
  }
}

function restoreFocus(): void {
  if (returnFocus?.isConnected) returnFocus.focus();
  else document.querySelector<HTMLElement>('[data-testid="v1-board-refresh"], [data-testid="nav-results"]')?.focus();
  returnFocus = null;
}
</script>

<template>
  <v-dialog
    :model-value="target !== null" max-width="560" :persistent="busy || picking || unknown"
    :aria-label="t('results.rerun')" @update:model-value="open => { if (!open) rerun.closeRerun(); }" @after-enter="focusCancel" @after-leave="restoreFocus"
  >
    <v-card v-if="target" class="u-v1-dialog" variant="flat" color="surface" elevation="8" :title="t('results.rerun')" data-testid="rerun-dialog" :data-step="state.step">
      <v-card-text class="u-stack u-break">
        <p>{{ t("results.rerunCreatesJob") }}</p>
        <p>{{ t("results.rerunOf") }} {{ target.jobId }}</p>
        <template v-if="state.step !== 'accepted'">
          <p v-if="target.path">{{ t("results.rerunInput", { path: target.path }) }}</p>
          <v-btn variant="outlined" size="small" :loading="picking" :disabled="picking || busy || unknown || sourceGone" data-testid="rerun-pick" @click="pick">{{ t("results.rerunPick") }}</v-btn>
          <p v-if="pickerError" role="status">{{ t("tx.pick.failed") }}</p>
          <p>{{ t("v1.field.preset") }} · {{ settings.preset.item?.name ?? '—' }}</p>
          <p>{{ t("v1.field.policy") }} · {{ settings.policy.item?.name ?? '—' }}</p>
          <p v-if="hasSourceRow" data-testid="rerun-settings-source">{{ t("results.rerunSourceRowSettings") }}</p>
          <v-alert v-else type="info" density="compact" role="status" data-testid="rerun-settings-default">
            {{ t("results.rerunCommonSettings") }}
          </v-alert>
          <p v-if="evaluation.files[0]">{{ t("results.rerunOutput", { path: evaluation.files[0].outputDir }) }}</p>
          <p v-else-if="target.path && !busy" role="status">{{ t("results.rerunSettings") }}</p>
          <p v-if="busy" role="status">{{ t("results.rerunPending") }}</p>
          <template v-if="state.step === 'resolved'">
            <p v-if="rerun.isStale()" role="status">{{ t("v1.batch.stale") }}</p>
            <p v-else-if="expired" role="status">{{ t("v1.batch.expired") }}</p>
            <p v-else-if="state.prepared?.resolution?.files[0]?.spec">
              {{ state.prepared.resolution.files[0].spec.modelId }} · {{ state.prepared.resolution.files[0].spec.device }}
            </p>
          </template>
          <v-alert v-if="state.step === 'rejected'" type="warning" density="compact" role="status">
            <p>{{ sourceGone ? t("results.rerunSourceChanged") : state.error?.code ? reason(state.error.code) : t("v1.batch.mismatch") }}</p>
            <ul v-if="state.error?.issues.length" class="u-notes">
              <li v-for="item in state.error.issues" :key="item.path + item.code">{{ issue(item) }}</li>
            </ul>
          </v-alert>
          <v-alert v-if="unknown" type="warning" density="compact" role="status">{{ t("v1.batch.unknown") }}</v-alert>
        </template>
        <v-alert v-else type="success" density="compact" role="status">{{ t("v1.batch.accepted", { count: 1 }) }} {{ state.jobs[0]?.id }}</v-alert>
      </v-card-text>
      <v-card-actions class="u-row flex-wrap">
        <v-btn ref="cancelButton" :disabled="picking || busy || unknown" data-testid="rerun-close" @click="rerun.closeRerun()">
          {{ t(state.step === 'accepted' ? "v1.board.action.close" : "common.cancel") }}
        </v-btn>
        <v-btn v-if="unknown" color="primary" data-testid="rerun-recheck" @click="rerun.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
        <template v-else-if="state.step !== 'accepted'">
          <v-btn variant="outlined" :disabled="!canPrepare || picking" data-testid="rerun-check" @click="rerun.prepare()">{{ t("results.rerunCheck") }}</v-btn>
          <v-btn color="primary" :disabled="picking || expired || gate.owner !== null || !rerun.canSubmit()" data-testid="rerun-submit" @click="rerun.submit()">{{ t("results.rerun") }}</v-btn>
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
