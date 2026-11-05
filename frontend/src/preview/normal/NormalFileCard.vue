<script setup lang="ts">
import { mdiFileMusicOutline, mdiFilePlusOutline, mdiFileReplaceOutline } from "@mdi/js";
import AreaState from "../components/AreaState.vue";
import { usePreview } from "../composables";
import { formatDuration } from "../domain/fixtures";
import { useNormalFile } from "./useNormalFile";

/**
 * The one file of Normal mode. "Choose another file" swaps what this card shows; inputs that exist
 * for Expert (other files, their jobs and settings) stay untouched and are simply not shown here.
 * Picking a file is a demo: it takes the next synthetic name and opens no dialog.
 */
const { demo, state, t } = usePreview();
const { input } = useNormalFile();
</script>

<template>
  <v-card class="p-card" tag="section" aria-labelledby="normal-file-heading" data-testid="normal-file">
    <div class="p-card__head"><h2 id="normal-file-heading" class="p-card__title">{{ t("normal.file") }}</h2></div>
    <AreaState :state="state.inputsArea" :empty="false" empty-text="" testid="normal-inputs" @retry="demo.recoverArea('inputs')">
      <div v-if="!input" class="p-dropzone" data-testid="normal-empty">
        <v-icon :icon="mdiFilePlusOutline" size="large" aria-hidden="true" />
        <p class="text-body-large">{{ t("normal.emptyHint") }}</p>
        <v-btn color="primary" :prepend-icon="mdiFilePlusOutline" data-testid="normal-choose" @click="demo.chooseNormalFile()">{{ t("normal.choose") }}</v-btn>
      </div>
      <template v-else>
        <div class="p-filebox" :data-testid="`normal-input-${input.id}`">
          <v-icon :icon="mdiFileMusicOutline" aria-hidden="true" />
          <div class="p-filebox__text">
            <div class="text-body-large font-weight-medium p-ellipsis" :title="input.name" data-testid="normal-file-name">{{ input.name }}</div>
            <div class="text-body-medium">
              {{ input.format.toUpperCase() }} · {{ formatDuration(input.durationSec) }}
              <template v-if="!input.supported"> · <strong>{{ t("input.unsupported") }}</strong></template>
            </div>
          </div>
        </div>
        <v-btn variant="outlined" :prepend-icon="mdiFileReplaceOutline" data-testid="normal-choose-other" @click="demo.chooseNormalFile()">{{ t("normal.chooseOther") }}</v-btn>
      </template>
    </AreaState>
  </v-card>
</template>
