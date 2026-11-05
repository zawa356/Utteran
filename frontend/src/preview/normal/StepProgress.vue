<script setup lang="ts">
import { mdiClose, mdiReplay } from "@mdi/js";
import AreaState from "../components/AreaState.vue";
import JobStateChip from "../components/JobStateChip.vue";
import QueueToolbar from "../components/QueueToolbar.vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";
import StepCard from "./StepCard.vue";

/** Per-input state and stage. No percentage (G-03): the backend does not send progress. */
const { demo, state, t } = usePreview();
</script>

<template>
  <StepCard :n="4" :title="t('step.progress')" testid="step-progress">
    <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('progress.empty')" testid="normal-progress" @retry="demo.recoverArea('queue')">
      <QueueToolbar class="mb-2" />
      <v-table density="compact" class="p-table" data-testid="normal-progress-table">
        <thead>
          <tr>
            <th>{{ t("inputs.col.name") }}</th>
            <th class="p-col-state">{{ t("progress.col.state") }}</th>
            <th class="p-col-stage">{{ t("progress.col.stage") }}</th>
            <th class="p-col-actions"><span class="p-sr-only">{{ t("inputs.col.actions") }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in state.jobs" :key="job.id" :data-testid="`normal-job-${job.id}`">
            <td class="p-cell-name"><span class="p-ellipsis" :title="job.inputName">{{ job.inputName }}</span></td>
            <td><JobStateChip :state="job.state" /></td>
            <td class="text-body-2">
              {{ job.stage ? t(`job.stage.${job.stage}`) : t("job.noStage") }}
              <span v-if="job.state === 'failed'" class="text-caption"> — {{ t("job.failedShort") }}</span>
            </td>
            <td class="text-end">
              <v-btn v-if="isActive(job)" size="x-small" variant="text" :prepend-icon="mdiClose" @click="demo.cancel(job.id)">{{ t("job.cancel") }}</v-btn>
              <v-btn
                v-if="job.state === 'failed' || job.state === 'cancelled'"
                size="x-small"
                variant="tonal"
                color="primary"
                :prepend-icon="mdiReplay"
                :data-testid="`normal-retry-${job.id}`"
                @click="demo.retry(job.id)"
              >
                {{ t("job.retry") }}
              </v-btn>
              <v-btn
                v-if="job.state === 'preparing' || job.state === 'running'"
                size="x-small"
                variant="outlined"
                :data-testid="`normal-fail-${job.id}`"
                @click="demo.fail(job.id)"
              >
                {{ t("demo.fail") }}
              </v-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
      <p class="text-caption mt-1">{{ t("job.noPercent") }}</p>
    </AreaState>
  </StepCard>
</template>
