<script setup lang="ts">
import {
  mdiAlertOutline,
  mdiArrowRight,
  mdiClose,
  mdiFileDocumentOutline,
  mdiFilePlusOutline,
  mdiFolderOpenOutline,
  mdiInformationOutline,
  mdiPercentOutline,
  mdiPlay,
  mdiReplay,
  mdiStepForward,
} from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobProgress from "../components/JobProgress.vue";
import JobStageTrack from "../components/JobStageTrack.vue";
import JobStateChip from "../components/JobStateChip.vue";
import JobStatusNotice from "../components/JobStatusNotice.vue";
import { usePreview } from "../composables";
import { isActive, outputNames } from "../domain/demo";
import { useNormalFile } from "./useNormalFile";
import { useSetupNav } from "../wizard/useSetupNav";

/**
 * Start → progress → result of the ONE file (shortest path; no queue, no history). Short labels
 * instead of explanations; the reason is shown only when "start" is unavailable. The demo controls
 * that move the fictional job are grouped behind a dashed line.
 */
const { demo, layout, state, t } = usePreview();
const { openSetup } = useSetupNav();
const { input, job, blocked, slowNote, repairNote } = useNormalFile();
const active = computed(() => job.value !== null && isActive(job.value));
const outputs = computed(() => (job.value?.state === "succeeded" ? outputNames(job.value) : []));
</script>

<template>
  <v-card class="p-card" tag="section" aria-labelledby="normal-run-heading" data-testid="normal-run">
    <div class="p-card__head">
      <h2 id="normal-run-heading" class="p-card__title">{{ t("normal.run") }}</h2>
      <v-spacer />
      <JobStateChip v-if="job" :state="job.state" />
    </div>
    <AreaState :state="state.queueArea" :empty="false" empty-text="" testid="normal-progress" @retry="demo.recoverArea('queue')">
      <!-- not started -->
      <div v-if="!job" class="u-stack">
        <v-btn color="primary" size="large" block :prepend-icon="mdiPlay" :disabled="blocked !== null" data-testid="normal-start" @click="input && demo.submit([input.id])">
          {{ t("start.button") }}
        </v-btn>
        <div v-if="blocked" class="p-reason" role="status" data-testid="normal-start-blocked">
          <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
          <span>{{ blocked.text }}</span>
          <v-btn v-if="blocked.diarization && input" variant="text" color="primary" data-testid="normal-diarization-off" @click="demo.setNormalSetting(input.id, { diarization: false })">{{ t("start.diarizationOff") }}</v-btn>
          <v-btn v-if="blocked.setup" variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="normal-blocked-setup" @click="layout.navigate('setup')">{{ t("section.setup") }}</v-btn>
        </div>
        <div v-else-if="slowNote" class="p-reason" data-testid="normal-slow-note">
          <v-icon :icon="mdiInformationOutline" size="small" aria-hidden="true" />
          <span>{{ slowNote }}</span>
        </div>
        <div v-if="!blocked && repairNote" class="p-reason" data-testid="normal-repair-note">
          <v-icon :icon="mdiInformationOutline" size="small" aria-hidden="true" />
          <span>{{ repairNote }}</span>
          <v-btn variant="text" color="primary" data-testid="normal-repair" @click="openSetup('repair')">{{ t("env.action.repair") }}</v-btn>
        </div>
      </div>

      <!-- submitted -->
      <div v-else class="u-stack" :data-testid="`normal-job-${job.id}`">
        <JobStageTrack :job="job" />
        <JobProgress v-if="active" :job="job" />
        <JobStatusNotice :active="active" />
        <v-alert v-if="job.state === 'failed'" type="error" role="alert" data-testid="normal-job-error">
          {{ t("job.failedBody") }}
          <div class="text-body-small">{{ t("area.errorCode", { code: job.errorCode ?? "—" }) }}</div>
        </v-alert>
        <div v-if="active || job.state === 'failed' || job.state === 'cancelled'" class="u-row">
          <v-btn v-if="active" variant="outlined" :prepend-icon="mdiClose" data-testid="normal-cancel" @click="demo.cancel(job.id)">{{ t("job.cancel") }}</v-btn>
          <v-btn v-if="job.state === 'failed' || job.state === 'cancelled'" color="primary" :prepend-icon="mdiReplay" :data-testid="`normal-retry-${job.id}`" @click="demo.retry(job.id)">{{ t("job.retry") }}</v-btn>
        </div>

        <div v-if="outputs.length > 0" class="u-stack" data-testid="normal-result">
          <h3 class="text-body-large font-weight-medium">{{ t("normal.result") }}</h3>
          <ul class="p-outputs" :data-testid="`normal-result-${job.id}`">
            <li v-for="name in outputs" :key="name"><v-icon :icon="mdiFileDocumentOutline" size="small" aria-hidden="true" /> <span class="p-ellipsis" :title="name">{{ name }}</span></li>
          </ul>
          <div class="u-row">
            <v-btn variant="tonal" :prepend-icon="mdiFolderOpenOutline" disabled :title="t('result.folderDisabled')" data-testid="normal-open-folder">{{ t("result.folder") }}</v-btn>
            <v-btn variant="text" color="primary" :prepend-icon="mdiFilePlusOutline" data-testid="normal-next-file" @click="demo.chooseNormalFile()">{{ t("normal.nextFile") }}</v-btn>
          </div>
        </div>

        <div v-if="active" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
          <v-chip label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
          <v-btn v-if="job.state === 'running' && job.progress.fraction !== null && job.progress.fraction < 1" variant="outlined" :prepend-icon="mdiPercentOutline" data-testid="normal-advance" @click="demo.advance(job.id)">{{ t("demo.advance") }}</v-btn>
          <v-btn variant="outlined" :prepend-icon="mdiStepForward" data-testid="normal-step" @click="demo.step(job.id)">{{ t("demo.step") }}</v-btn>
          <v-btn v-if="job.state !== 'waiting'" variant="outlined" :prepend-icon="mdiAlertOutline" data-testid="normal-fail" @click="demo.fail(job.id)">{{ t("demo.fail") }}</v-btn>
        </div>
      </div>
    </AreaState>
  </v-card>
</template>
