<script setup lang="ts">
import { mdiDeleteOutline, mdiFolderOpenOutline, mdiPlaylistPlus, mdiRefresh, mdiReplay, mdiTextBoxSearchOutline } from "@mdi/js";
import { computed, ref } from "vue";
import JobOutputs from "../components/JobOutputs.vue";
import JobStateChip from "../components/JobStateChip.vue";
import { useSnapshotText } from "../components/useSnapshotText";
import { usePreview } from "../composables";
import type { DemoJob } from "../domain/demo";

/**
 * One finished job in Results & history: outputs, frozen settings and folder, and what can be done.
 * "Remove from history" is a confirmation MOCK: deleting history (and whether the frozen snapshot is
 * deleted with it) is an open owner decision (retention / privacy), and output files are never
 * deleted from here. Its final button stays disabled.
 */
const props = defineProps<{ job: DemoJob }>();
const { demo, state, t } = usePreview();
const { snapshotText } = useSnapshotText();
const deleting = ref(false);
const inputGone = computed(() => !state.value.inputs.some((i) => i.id === props.job.inputId));
</script>

<template>
  <v-card class="pa-3 u-stack" data-testid="result-detail" :data-job="job.id">
    <div class="u-row">
      <h2 class="u-subheading p-ellipsis" :title="job.inputName">{{ job.inputName }}</h2>
      <JobStateChip :state="job.state" />
    </div>
    <dl class="p-dl text-body-medium">
      <dt>{{ t("job.id") }}</dt>
      <dd data-testid="result-job-id">{{ job.id }}</dd>
      <dt>{{ t("job.runLabel") }}</dt>
      <dd>{{ t("job.runNo", { n: job.runNo }) }}</dd>
      <dt>{{ t("job.snapshot") }}</dt>
      <dd data-testid="result-snapshot">{{ snapshotText(job.snapshot) }}</dd>
      <template v-if="job.state === 'failed'">
        <dt>{{ t("history.error") }}</dt>
        <dd>{{ job.errorCode ?? "—" }}</dd>
      </template>
    </dl>
    <JobOutputs v-if="job.state === 'succeeded'" :job="job" />
    <div class="u-row">
      <template v-if="job.state === 'succeeded'">
        <v-btn variant="tonal" :prepend-icon="mdiTextBoxSearchOutline" disabled>{{ t("results.viewer") }}</v-btn>
        <v-btn variant="tonal" :prepend-icon="mdiRefresh" disabled>{{ t("results.reexport") }}</v-btn>
        <v-btn variant="tonal" :prepend-icon="mdiFolderOpenOutline" disabled>{{ t("result.folder") }}</v-btn>
      </template>
      <v-btn v-else color="primary" variant="tonal" :prepend-icon="mdiReplay" data-testid="result-retry" @click="demo.retry(job.id)">{{ t("job.retry") }}</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiPlaylistPlus" :disabled="inputGone" data-testid="result-rerun" @click="demo.rerun(job.id)">{{ t("job.rerun") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiDeleteOutline" data-testid="result-delete" @click="deleting = true">{{ t("history.delete") }}</v-btn>
    </div>
    <p v-if="job.state === 'succeeded'" class="text-body-small" data-testid="results-later">{{ t("results.later") }}</p>

    <v-dialog v-model="deleting" max-width="480" data-testid="history-delete-dialog">
      <v-card variant="elevated" :title="t('history.deleteTitle')" data-testid="history-delete-card">
        <v-card-text class="u-stack">
          <dl class="p-dl text-body-medium">
            <dt>{{ t("history.deleteTarget") }}</dt>
            <dd>{{ job.inputName }} ({{ job.id }})</dd>
          </dl>
          <p>{{ t("history.deleteKeepsFiles") }}</p>
          <p class="text-body-medium" data-testid="history-delete-later">{{ t("history.deleteLater") }}</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-testid="history-delete-cancel" @click="deleting = false">{{ t("common.cancel") }}</v-btn>
          <v-btn color="error" variant="tonal" disabled data-testid="history-delete-confirm">{{ t("history.deleteConfirm") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>
