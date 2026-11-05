<script setup lang="ts">
import { mdiAlertOutline, mdiArrowRight, mdiClose, mdiFileDocumentOutline, mdiFolderOpenOutline, mdiPlay, mdiReplay, mdiStepForward } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobStageTrack from "../components/JobStageTrack.vue";
import JobStateChip from "../components/JobStateChip.vue";
import { usePreview } from "../composables";
import { isActive, outputNames } from "../domain/demo";
import { useNormalFile } from "./useNormalFile";

/**
 * Start → state → result of the ONE file, in one card (shortest path; no queue, no history).
 * Demo-only controls are grouped behind a dashed line and labelled.
 */
const { demo, layout, state, t } = usePreview();
const { input, job, blocked } = useNormalFile();
const active = computed(() => job.value !== null && isActive(job.value));
const outputs = computed(() => (job.value?.state === "succeeded" ? outputNames(job.value) : []));
</script>

<template>
  <v-card class="pa-3" tag="section" aria-labelledby="normal-run-heading" data-testid="normal-run">
    <div class="u-row mb-2">
      <h2 id="normal-run-heading" class="u-subheading">{{ t("normal.run") }}</h2>
      <v-spacer />
      <JobStateChip v-if="job" :state="job.state" />
      <v-chip v-else size="small" label variant="outlined" data-testid="normal-not-started">{{ t("normal.notStarted") }}</v-chip>
    </div>
    <AreaState :state="state.queueArea" :empty="false" empty-text="" testid="normal-progress" @retry="demo.recoverArea('queue')">
      <!-- not started -->
      <div v-if="!job" class="u-stack">
        <v-btn color="primary" size="large" block :prepend-icon="mdiPlay" :disabled="blocked !== null" data-testid="normal-start" @click="input && demo.submit([input.id])">
          {{ t("start.button") }}
        </v-btn>
        <div v-if="blocked" class="p-reason" role="status" data-testid="normal-start-blocked">
          <v-icon :icon="mdiAlertOutline" size="small" aria-hidden="true" />
          <span class="text-body-medium">{{ blocked }}</span>
          <v-btn
            v-if="state.envExample === 'unconfigured' || state.envExample === 'error'"
            size="x-small"
            variant="text"
            color="primary"
            :append-icon="mdiArrowRight"
            data-testid="normal-blocked-setup"
            @click="layout.navigate('setup')"
          >
            {{ t("section.setup") }}
          </v-btn>
        </div>
        <p class="text-body-small" data-testid="normal-start-demo">{{ t("start.demoNote") }}</p>
      </div>

      <!-- submitted -->
      <div v-else class="u-stack" :data-testid="`normal-job-${job.id}`">
        <JobStageTrack :job="job" />
        <p v-if="active" class="text-body-small">{{ t("job.noPercent") }}</p>
        <v-alert v-if="job.state === 'failed'" type="error" role="alert" data-testid="normal-job-error">
          {{ t("job.failedBody") }}
          <div class="text-body-small">{{ t("area.errorCode", { code: job.errorCode ?? "—" }) }}</div>
        </v-alert>
        <div class="u-row">
          <v-btn v-if="active" variant="tonal" :prepend-icon="mdiClose" data-testid="normal-cancel" @click="demo.cancel(job.id)">{{ t("job.cancel") }}</v-btn>
          <v-btn v-if="job.state === 'failed' || job.state === 'cancelled'" color="primary" :prepend-icon="mdiReplay" :data-testid="`normal-retry-${job.id}`" @click="demo.retry(job.id)">{{ t("job.retry") }}</v-btn>
        </div>

        <div v-if="outputs.length > 0" class="u-stack" data-testid="normal-result">
          <h3 class="text-body-large font-weight-medium">{{ t("normal.result") }}</h3>
          <ul class="p-outputs" :data-testid="`normal-result-${job.id}`">
            <li v-for="name in outputs" :key="name"><v-icon :icon="mdiFileDocumentOutline" size="x-small" aria-hidden="true" /> <span class="p-ellipsis" :title="name">{{ name }}</span></li>
          </ul>
          <p class="text-body-small">{{ t("result.syntheticNote") }}</p>
          <div class="u-row">
            <v-btn variant="tonal" :prepend-icon="mdiFolderOpenOutline" disabled data-testid="normal-open-folder">{{ t("result.folder") }}</v-btn>
            <span class="text-body-small">{{ t("result.folderDisabled") }}</span>
          </div>
          <div class="u-row">
            <v-btn variant="text" color="primary" :prepend-icon="mdiPlay" data-testid="normal-next-file" @click="demo.chooseNormalFile()">{{ t("normal.nextFile") }}</v-btn>
            <v-btn variant="text" :append-icon="mdiArrowRight" data-testid="normal-open-results" @click="layout.openInExpert('results')">{{ t("normal.openResults") }}</v-btn>
          </div>
        </div>

        <div v-if="active" class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
          <v-chip size="x-small" label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
          <v-btn size="small" variant="outlined" :prepend-icon="mdiStepForward" data-testid="normal-step" @click="demo.step(job.id)">{{ t("demo.step") }}</v-btn>
          <v-btn v-if="job.state !== 'waiting'" size="small" variant="outlined" :prepend-icon="mdiAlertOutline" data-testid="normal-fail" @click="demo.fail(job.id)">{{ t("demo.fail") }}</v-btn>
        </div>
      </div>
    </AreaState>
  </v-card>
</template>
