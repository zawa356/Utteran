<script setup lang="ts">
import { mdiDeleteOutline, mdiFilePlusOutline, mdiFolderPlusOutline, mdiSelectAll, mdiSelectRemove } from "@mdi/js";
import { computed, ref } from "vue";
import AreaState from "../components/AreaState.vue";
import InputTable from "../components/InputTable.vue";
import PaneHeading from "../components/PaneHeading.vue";
import { usePreview } from "../composables";

/**
 * Step 1 (left pane): add files and choose which ones the next steps act on. The heading shows
 * the selection count that the Inspector (step 2) and the submit button (step 3) use as well.
 * Adding is a demo: it appends synthetic names and opens no dialog.
 */
const { demo, state, t } = usePreview();
const confirming = ref(false);
const selected = computed(() => state.value.selected.length);
function confirmRemove() {
  demo.removeSelected();
  confirming.value = false;
}
</script>

<template>
  <div class="p-pane__body">
    <PaneHeading id="pane-inputs-heading" :step="1" :title="t('expert.inputs')" :meta="t('inputs.selectedOf', { count: selected, total: state.inputs.length })" />
    <div class="p-add" role="group" :aria-label="t('inputs.addGroup')">
      <v-btn color="primary" variant="tonal" :prepend-icon="mdiFilePlusOutline" class="p-add__btn" data-testid="expert-add-file" @click="demo.addFiles(1)">{{ t("inputs.addFile") }}</v-btn>
      <v-btn color="primary" variant="tonal" :prepend-icon="mdiFolderPlusOutline" class="p-add__btn" data-testid="expert-add-folder" @click="demo.addFiles(3)">{{ t("inputs.addFolder") }}</v-btn>
    </div>
    <div class="p-selbar">
      <span class="text-body-medium" data-testid="inputs-heading">{{ t("inputs.count", { count: state.inputs.length }) }}</span>
      <v-spacer />
      <v-btn size="small" variant="text" :icon="mdiSelectAll" :disabled="state.inputs.length === 0" :aria-label="t('inputs.selectAll')" :title="t('inputs.selectAll')" data-testid="inputs-select-all" @click="demo.selectAll()" />
      <v-btn size="small" variant="text" :icon="mdiSelectRemove" :disabled="selected === 0" :aria-label="t('inputs.clearSelection')" :title="t('inputs.clearSelection')" data-testid="inputs-clear-selection" @click="demo.clearSelection()" />
      <v-btn size="small" variant="text" :icon="mdiDeleteOutline" :disabled="selected === 0" :aria-label="t('inputs.removeSelected')" :title="t('inputs.removeSelected')" data-testid="inputs-remove" @click="confirming = true" />
    </div>
    <AreaState :state="state.inputsArea" :empty="state.inputs.length === 0" :empty-text="t('inputs.empty')" testid="expert-inputs" @retry="demo.recoverArea('inputs')">
      <InputTable />
    </AreaState>
    <v-dialog v-model="confirming" max-width="440" data-testid="remove-dialog">
      <v-card variant="elevated" :title="t('inputs.removeTitle')" data-testid="remove-dialog-card">
        <v-card-text>{{ t("inputs.removeBody", { count: selected }) }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-testid="remove-cancel" @click="confirming = false">{{ t("common.cancel") }}</v-btn>
          <v-btn color="error" variant="tonal" data-testid="remove-confirm" @click="confirmRemove">{{ t("inputs.removeConfirm") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
