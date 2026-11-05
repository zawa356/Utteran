<script setup lang="ts">
import { mdiArrowRight, mdiClose, mdiFileMusicOutline, mdiFileReplaceOutline, mdiFilePlusOutline } from "@mdi/js";
import AreaState from "../components/AreaState.vue";
import { usePreview } from "../composables";
import { formatDuration } from "../domain/fixtures";
import { useNormalFile } from "./useNormalFile";

/**
 * The one input of Normal mode. With several inputs (made in Expert) it shows a representative
 * and only POINTS to Expert: nothing is removed, submitted or overwritten implicitly.
 */
const { demo, layout, state, t } = usePreview();
const { input, job, others, overrides } = useNormalFile();
</script>

<template>
  <v-card class="pa-3" tag="section" aria-labelledby="normal-file-heading" data-testid="normal-file">
    <div class="u-row mb-2">
      <h2 id="normal-file-heading" class="u-subheading">{{ t("normal.file") }}</h2>
      <v-spacer />
      <v-btn
        v-if="input"
        size="small"
        variant="text"
        :prepend-icon="mdiFileReplaceOutline"
        :disabled="state.inputsArea !== 'ready'"
        data-testid="normal-choose-other"
        @click="demo.chooseNormalFile()"
      >
        {{ t("normal.chooseOther") }}
      </v-btn>
    </div>
    <AreaState :state="state.inputsArea" :empty="false" empty-text="" testid="normal-inputs" @retry="demo.recoverArea('inputs')">
      <div v-if="!input" class="p-dropzone" data-testid="normal-empty">
        <v-icon :icon="mdiFilePlusOutline" size="large" aria-hidden="true" />
        <p class="text-body-medium">{{ t("normal.emptyHint") }}</p>
        <v-btn color="primary" :prepend-icon="mdiFilePlusOutline" data-testid="normal-choose" @click="demo.chooseNormalFile()">{{ t("normal.choose") }}</v-btn>
        <p class="text-body-small">{{ t("inputs.demoPicker") }}</p>
      </div>
      <div v-else class="p-filebox" :data-testid="`normal-input-${input.id}`">
        <v-icon :icon="mdiFileMusicOutline" aria-hidden="true" />
        <div class="p-filebox__text">
          <div class="text-body-large font-weight-medium p-ellipsis" :title="input.name" data-testid="normal-file-name">{{ input.name }}</div>
          <div class="text-body-small">
            {{ input.format.toUpperCase() }} · {{ formatDuration(input.durationSec) }}
            <template v-if="!input.supported"> · <strong>{{ t("input.unsupported") }}</strong></template>
            <template v-if="overrides > 0"> · {{ t("normal.hasOverrides", { count: overrides }) }}</template>
          </div>
        </div>
        <v-btn
          size="small"
          variant="text"
          :icon="mdiClose"
          :disabled="job !== null && (job.state === 'waiting' || job.state === 'preparing' || job.state === 'running')"
          :aria-label="t('inputs.remove', { name: input.name })"
          :title="t('inputs.remove', { name: input.name })"
          data-testid="normal-remove"
          @click="demo.remove([input.id])"
        />
      </div>
      <v-alert v-if="others > 0" type="info" variant="outlined" class="mt-2" data-testid="normal-multi-note">
        {{ t("normal.multiNote", { count: others }) }}
        <template #append>
          <v-btn size="small" variant="text" color="primary" :append-icon="mdiArrowRight" data-testid="normal-open-expert" @click="layout.openInExpert('transcribe')">{{ t("normal.openExpert") }}</v-btn>
        </template>
      </v-alert>
    </AreaState>
  </v-card>
</template>
