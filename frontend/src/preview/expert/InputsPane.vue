<script setup lang="ts">
import { mdiDeleteOutline, mdiFilePlusOutline, mdiFolderPlusOutline, mdiSelectAll, mdiSelectRemove } from "@mdi/js";
import { ref } from "vue";
import AreaState from "../components/AreaState.vue";
import InputRow from "../components/InputRow.vue";
import { usePreview } from "../composables";

/** Left pane: add (demo pool), list, multi-select, remove with confirmation. */
const { demo, state, t } = usePreview();
const confirming = ref(false);
function confirmRemove() {
  demo.removeSelected();
  confirming.value = false;
}
</script>

<template>
  <div class="u-stack">
    <div class="u-row">
      <h2 class="u-subheading" data-testid="inputs-heading">{{ t("expert.inputs") }} ({{ state.inputs.length }})</h2>
      <v-spacer />
      <span class="text-caption" data-testid="inputs-selected-count">{{ t("inputs.selectedCount", { count: state.selected.length }) }}</span>
    </div>
    <div class="u-row">
      <v-btn size="small" variant="tonal" :prepend-icon="mdiFilePlusOutline" data-testid="expert-add-file" @click="demo.addFiles(1)">{{ t("inputs.addFile") }}</v-btn>
      <v-btn size="small" variant="tonal" :prepend-icon="mdiFolderPlusOutline" data-testid="expert-add-folder" @click="demo.addFiles(3)">{{ t("inputs.addFolder") }}</v-btn>
    </div>
    <div class="u-row">
      <v-btn size="small" variant="text" :prepend-icon="mdiSelectAll" :disabled="state.inputs.length === 0" data-testid="inputs-select-all" @click="demo.selectAll()">{{ t("inputs.selectAll") }}</v-btn>
      <v-btn size="small" variant="text" :prepend-icon="mdiSelectRemove" :disabled="state.selected.length === 0" data-testid="inputs-clear-selection" @click="demo.clearSelection()">{{ t("inputs.clearSelection") }}</v-btn>
      <v-btn size="small" variant="text" :prepend-icon="mdiDeleteOutline" :disabled="state.selected.length === 0" data-testid="inputs-remove" @click="confirming = true">{{ t("inputs.removeSelected") }}</v-btn>
    </div>
    <AreaState :state="state.inputsArea" :empty="state.inputs.length === 0" :empty-text="t('inputs.empty')" testid="expert-inputs" @retry="demo.recoverArea('inputs')">
      <v-list :aria-label="t('inputs.listLabel')" class="p-input-list" data-testid="input-list">
        <InputRow v-for="i in state.inputs" :key="i.id" :input="i" />
      </v-list>
    </AreaState>
    <p class="text-caption">{{ t("inputs.overrideLegend") }}</p>
    <v-dialog v-model="confirming" max-width="420" data-testid="remove-dialog">
      <v-card :title="t('inputs.removeTitle')" data-testid="remove-dialog-card">
        <v-card-text>{{ t("inputs.removeBody", { count: state.selected.length }) }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-testid="remove-cancel" @click="confirming = false">{{ t("common.cancel") }}</v-btn>
          <v-btn color="error" variant="tonal" data-testid="remove-confirm" @click="confirmRemove">{{ t("inputs.removeConfirm") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
