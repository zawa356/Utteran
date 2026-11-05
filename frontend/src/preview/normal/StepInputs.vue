<script setup lang="ts">
import { mdiClose, mdiFilePlusOutline, mdiFolderPlusOutline } from "@mdi/js";
import { computed } from "vue";
import AreaState from "../components/AreaState.vue";
import { usePreview } from "../composables";
import { overrideCount } from "../domain/demo";
import { formatDuration } from "../domain/fixtures";
import StepCard from "./StepCard.vue";

const { demo, state, t } = usePreview();
const summary = computed(() => {
  const inputs = state.value.inputs;
  const supported = inputs.filter((i) => i.supported);
  const total = supported.reduce((sum, i) => sum + (i.durationSec ?? 0), 0);
  return t("inputs.summary", { count: inputs.length, supported: supported.length, unsupported: inputs.length - supported.length, total: formatDuration(total) });
});
</script>

<template>
  <StepCard :n="1" :title="t('step.inputs')" testid="step-inputs">
    <template #actions>
      <v-btn size="small" variant="tonal" :prepend-icon="mdiFilePlusOutline" data-testid="normal-add-file" @click="demo.addFiles(1)">{{ t("inputs.addFile") }}</v-btn>
      <v-btn size="small" variant="tonal" :prepend-icon="mdiFolderPlusOutline" data-testid="normal-add-folder" @click="demo.addFiles(3)">{{ t("inputs.addFolder") }}</v-btn>
    </template>
    <p class="text-caption mb-2">{{ t("inputs.demoPicker") }}</p>
    <AreaState
      :state="state.inputsArea"
      :empty="state.inputs.length === 0"
      :empty-text="t('inputs.empty')"
      testid="normal-inputs"
      @retry="demo.recoverArea('inputs')"
    >
      <div class="text-body-2 mb-1" data-testid="normal-inputs-summary">{{ summary }}</div>
      <v-table density="compact" class="p-table" data-testid="normal-inputs-table">
        <thead>
          <tr>
            <th>{{ t("inputs.col.name") }}</th>
            <th class="p-col-format">{{ t("inputs.col.format") }}</th>
            <th class="text-end p-col-duration">{{ t("inputs.col.duration") }}</th>
            <th class="p-col-state">{{ t("inputs.col.status") }}</th>
            <th class="p-col-icon"><span class="p-sr-only">{{ t("inputs.col.actions") }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in state.inputs" :key="i.id" :data-testid="`normal-input-${i.id}`">
            <td class="p-cell-name"><span class="p-ellipsis" :title="i.name">{{ i.name }}</span></td>
            <td>{{ i.format.toUpperCase() }}</td>
            <td class="text-end">{{ formatDuration(i.durationSec) }}</td>
            <td>
              <v-chip v-if="!i.supported" size="x-small" label color="warning" variant="tonal">{{ t("input.unsupported") }}</v-chip>
              <v-chip v-else-if="overrideCount(state, i.id) > 0" size="x-small" label color="primary" variant="tonal">{{ t("inputs.hasOverride") }}</v-chip>
              <span v-else class="text-caption">{{ t("input.supported") }}</span>
            </td>
            <td class="text-end">
              <v-btn size="x-small" variant="text" :icon="mdiClose" :aria-label="t('inputs.remove', { name: i.name })" :data-testid="`normal-remove-${i.id}`" @click="demo.remove([i.id])" />
            </td>
          </tr>
        </tbody>
      </v-table>
      <div class="u-row mt-2">
        <v-btn size="small" variant="text" data-testid="normal-clear-inputs" @click="demo.remove(state.inputs.map((i) => i.id))">{{ t("inputs.clearAll") }}</v-btn>
        <span class="text-caption">{{ t("inputs.dropHint") }}</span>
      </div>
    </AreaState>
  </StepCard>
</template>
