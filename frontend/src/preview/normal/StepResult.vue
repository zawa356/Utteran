<script setup lang="ts">
import { mdiFolderOpenOutline, mdiTextBoxSearchOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import StepCard from "./StepCard.vue";

/** Synthetic result summary: example output names only. No file is written or opened. */
const { shell, state, t } = usePreview();
const done = computed(() => state.value.jobs.filter((j) => j.state === "succeeded"));
const outputs = (name: string, formats: string[]) => formats.map((f) => `${name.replace(/\.[^.]+$/, "")}.${f}`).join(", ");
</script>

<template>
  <StepCard :n="5" :title="t('step.result')" testid="step-result">
    <v-alert v-if="done.length === 0" type="info" variant="outlined" data-testid="normal-result-empty">{{ t("result.empty") }}</v-alert>
    <template v-else>
      <v-list density="compact" data-testid="normal-result-list">
        <v-list-item v-for="job in done" :key="job.id" :data-testid="`normal-result-${job.id}`">
          <template #title><span class="p-ellipsis" :title="job.inputName">{{ job.inputName }}</span></template>
          <template #subtitle>{{ t("result.outputs", { files: outputs(job.inputName, job.snapshot.formats) }) }}</template>
        </v-list-item>
      </v-list>
      <p class="text-caption">{{ t("result.syntheticNote") }}</p>
      <div class="u-row mt-2">
        <v-btn color="primary" variant="tonal" :prepend-icon="mdiTextBoxSearchOutline" data-testid="normal-open-results" @click="shell.navigate('results')">{{ t("result.open") }}</v-btn>
        <v-tooltip :text="t('result.folderDisabled')" location="top">
          <template #activator="{ props: tip }">
            <span v-bind="tip" tabindex="0" data-testid="normal-open-folder-wrap">
              <v-btn variant="text" :prepend-icon="mdiFolderOpenOutline" disabled data-testid="normal-open-folder">{{ t("result.folder") }}</v-btn>
            </span>
          </template>
        </v-tooltip>
        <span class="text-caption">{{ t("result.folderDisabled") }}</span>
      </div>
    </template>
  </StepCard>
</template>
