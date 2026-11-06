<script setup lang="ts">
import { mdiAlertCircleOutline, mdiCheckCircle, mdiCircleOutline, mdiPauseCircleOutline, mdiProgressClock, mdiStepForward, mdiStopCircleOutline, mdiAlertOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { currentStage, type StageStatus } from "../domain/setup";
import type { PreviewKey } from "../i18n";

/**
 * The (fictional) setup operation: one line per stage with its state, the running stage's progress,
 * and — when it stopped — the short reason and the three ways on: retry / change the choices / look
 * at the log. Never a dead end. Demo controls are behind the dashed line as everywhere else.
 */
const { layout, setup, setupState, t } = usePreview();
const ICON: Record<StageStatus, string> = {
  pending: mdiCircleOutline,
  running: mdiProgressClock,
  done: mdiCheckCircle,
  failed: mdiAlertCircleOutline,
  interrupted: mdiPauseCircleOutline,
};
const COLOR: Partial<Record<StageStatus, string>> = { done: "success", failed: "error", interrupted: "warning", running: "primary" };
const stage = computed(() => currentStage(setupState.value));
const doneCount = computed(() => setupState.value.stages.filter((s) => s.status === "done").length);
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-run" :data-run="setupState.run">
    <h2 class="p-wizard__title">{{ t(`wizard.run.title.${setupState.run}` as PreviewKey) }}</h2>
    <p class="text-body-medium" data-testid="wizard-run-count">{{ t("wizard.run.count", { done: doneCount, total: setupState.stages.length }) }}</p>
    <ol class="p-stages" data-testid="wizard-stages">
      <li v-for="st in setupState.stages" :key="st.id" :class="`p-stages__item--${st.status}`" :data-testid="`wizard-stage-${st.id}`" :data-status="st.status">
        <v-icon :icon="ICON[st.status]" :color="COLOR[st.status]" size="small" aria-hidden="true" />
        <span class="p-stages__name">{{ t(`stage.${st.id}` as PreviewKey) }}</span>
        <span class="text-body-medium">{{ t(`stageStatus.${st.status}` as PreviewKey) }}</span>
        <v-progress-linear
          v-if="st.status === 'running'"
          class="p-stages__bar"
          :model-value="st.fraction === null ? undefined : st.fraction * 100"
          :indeterminate="st.fraction === null"
          color="primary"
          height="6"
          rounded
          :aria-label="t(`stage.${st.id}` as PreviewKey)"
        />
      </li>
    </ol>

    <v-alert v-if="setupState.run === 'failed'" type="error" role="alert" :title="t('wizard.run.failedTitle', { stage: stage ? t(`stage.${stage.id}` as PreviewKey) : '—' })" data-testid="wizard-failed">
      <p>{{ t("wizard.run.failedBody") }}</p>
      <p class="text-body-small">{{ t("area.errorCode", { code: setupState.errorCode ?? "—" }) }}</p>
    </v-alert>
    <p v-else-if="setupState.run === 'interrupted'" class="text-body-medium" data-testid="wizard-interrupted">
      {{ t("wizard.run.interruptedBody", { stage: stage ? t(`stage.${stage.id}` as PreviewKey) : "—" }) }}
    </p>

    <div v-if="setupState.run === 'failed' || setupState.run === 'interrupted'" class="u-row" data-testid="wizard-recover">
      <v-btn color="primary" data-testid="wizard-continue" @click="setup.continueRun()">{{ setupState.run === "failed" ? t("wizard.run.retry") : t("wizard.run.resume") }}</v-btn>
      <v-btn variant="outlined" data-testid="wizard-change" @click="setup.changeChoices()">{{ t("wizard.run.change") }}</v-btn>
      <v-btn variant="text" data-testid="wizard-log" @click="layout.openDialog('setupLog')">{{ t("wizard.run.log") }}</v-btn>
    </div>

    <div v-if="setupState.run === 'running'" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
      <v-chip label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
      <v-btn variant="outlined" :prepend-icon="mdiStepForward" data-testid="wizard-demo-advance" @click="setup.advance()">{{ t("wizard.demo.advance") }}</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiAlertOutline" data-testid="wizard-demo-fail" @click="setup.fail()">{{ t("demo.fail") }}</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiStopCircleOutline" data-testid="wizard-demo-interrupt" @click="setup.interrupt()">{{ t("wizard.demo.interrupt") }}</v-btn>
    </div>
  </div>
</template>
