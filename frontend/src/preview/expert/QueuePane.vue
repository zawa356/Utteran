<script setup lang="ts">
import { mdiSendOutline } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import JobDetail from "../components/JobDetail.vue";
import QueueList from "../components/QueueList.vue";
import QueueToolbar from "../components/QueueToolbar.vue";
import SyncStatus from "../components/SyncStatus.vue";
import { usePreview } from "../composables";
import { submittedInputs } from "../domain/demo";

/** Right pane: submit (top, next to the Inspector), queue, selected job, connection state. */
const { demo, state, t } = usePreview();
// Only supported inputs that were never submitted (failed/cancelled jobs use "retry").
const open = computed(() => {
  const submitted = submittedInputs(state.value);
  return state.value.inputs.filter((i) => i.supported && !submitted.has(i.id));
});
const supportedSelected = computed(() => open.value.filter((i) => state.value.selected.includes(i.id)));
const supportedAll = open;
</script>

<template>
  <div class="u-stack">
    <h2 class="u-subheading">{{ t("expert.queue") }}</h2>
    <div class="u-row">
      <v-btn
        color="primary"
        size="small"
        :prepend-icon="mdiSendOutline"
        :disabled="supportedSelected.length === 0 || state.inputsArea !== 'ready'"
        data-testid="submit-selected"
        @click="demo.submit(supportedSelected.map((i) => i.id))"
      >
        {{ t("queue.submitSelected", { count: supportedSelected.length }) }}
      </v-btn>
      <v-btn
        size="small"
        variant="tonal"
        :disabled="supportedAll.length === 0 || state.inputsArea !== 'ready'"
        data-testid="submit-all"
        @click="demo.submit(supportedAll.map((i) => i.id))"
      >
        {{ t("queue.submitAll", { count: supportedAll.length }) }}
      </v-btn>
    </div>
    <p class="text-caption" data-testid="submit-demo-note">{{ t("start.demoNote") }}</p>
    <v-divider />
    <AreaState :state="state.queueArea" :empty="state.jobs.length === 0" :empty-text="t('queue.empty')" testid="expert-queue" @retry="demo.recoverArea('queue')">
      <QueueToolbar />
      <QueueList />
      <JobDetail />
    </AreaState>
    <v-divider />
    <SyncStatus />
  </div>
</template>
