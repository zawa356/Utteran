<script setup lang="ts">
import { mdiDeleteOutline, mdiFilePlusOutline, mdiFolderPlusOutline, mdiSelectAll, mdiSelectRemove } from "@mdi/js";
import { computed, ref } from "vue";
import AreaState from "../components/AreaState.vue";
import InputTable from "../components/InputTable.vue";
import { usePreview } from "../composables";
import { formatDuration } from "../domain/fixtures";

/**
 * Left pane. Adding inputs is the primary action of this pane: two large, labelled buttons at the
 * top (distinct from the queue pane's "submit"). Below, a dense Explorer-style detail list.
 */
const { demo, state, t } = usePreview();
const confirming = ref(false);
const total = computed(() => formatDuration(state.value.inputs.filter((i) => i.supported).reduce((s, i) => s + (i.durationSec ?? 0), 0)));
function confirmRemove() {
  demo.removeSelected();
  confirming.value = false;
}
</script>

<template>
  <div class="p-pane__body">
    <div class="p-pane__title">
      <h2 class="u-subheading" data-testid="inputs-heading">{{ t("expert.inputs") }} ({{ state.inputs.length }})</h2>
      <span class="text-body-small" data-testid="inputs-total">{{ t("inputs.total", { total }) }}</span>
    </div>
    <div class="p-add" role="group" :aria-label="t('inputs.addGroup')">
      <v-btn color="primary" variant="tonal" :prepend-icon="mdiFilePlusOutline" class="p-add__btn" data-testid="expert-add-file" @click="demo.addFiles(1)">{{ t("inputs.addFile") }}</v-btn>
      <v-btn color="primary" variant="tonal" :prepend-icon="mdiFolderPlusOutline" class="p-add__btn" data-testid="expert-add-folder" @click="demo.addFiles(3)">{{ t("inputs.addFolder") }}</v-btn>
    </div>
    <p class="text-body-small">{{ t("inputs.demoPickerShort") }}</p>
    <div class="p-selbar">
      <span class="text-body-small" data-testid="inputs-selected-count">{{ t("inputs.selectedCount", { count: state.selected.length }) }}</span>
      <v-spacer />
      <v-btn size="x-small" variant="text" :icon="mdiSelectAll" :disabled="state.inputs.length === 0" :aria-label="t('inputs.selectAll')" :title="t('inputs.selectAll')" data-testid="inputs-select-all" @click="demo.selectAll()" />
      <v-btn size="x-small" variant="text" :icon="mdiSelectRemove" :disabled="state.selected.length === 0" :aria-label="t('inputs.clearSelection')" :title="t('inputs.clearSelection')" data-testid="inputs-clear-selection" @click="demo.clearSelection()" />
      <v-btn size="x-small" variant="text" :icon="mdiDeleteOutline" :disabled="state.selected.length === 0" :aria-label="t('inputs.removeSelected')" :title="t('inputs.removeSelected')" data-testid="inputs-remove" @click="confirming = true" />
    </div>
    <AreaState :state="state.inputsArea" :empty="state.inputs.length === 0" :empty-text="t('inputs.empty')" testid="expert-inputs" @retry="demo.recoverArea('inputs')">
      <InputTable />
    </AreaState>
    <p class="text-body-small">{{ t("inputs.tableHelp") }}</p>
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
