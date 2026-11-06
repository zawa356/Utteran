<script setup lang="ts">
import { mdiFolderEditOutline, mdiFolderOutline, mdiUndoVariant } from "@mdi/js";
import { computed, ref } from "vue";
import { useJobText } from "../components/useJobText";
import { usePreview } from "../composables";
import FolderPickerDialog from "./FolderPickerDialog.vue";

/**
 * Where the outputs of these inputs go (#35; B5 `FileInput.output_dir` — a property of the INPUT,
 * not a processing setting, so presets never carry it). "Default" follows later changes of the
 * default; an individual folder does not. Jobs keep the folder they were submitted with.
 * Mixed selections show "several folders" and are only changed by an explicit action.
 */
const props = defineProps<{ inputIds: string[]; compact?: boolean }>();
const { demo, state, t } = usePreview();
const { dir } = useJobText();
const picking = ref(false);
const explicit = computed(() => props.inputIds.filter((id) => id in state.value.outputDirs));
const values = computed(() => [...new Set(props.inputIds.map((id) => state.value.outputDirs[id] ?? null))]);
const mixed = computed(() => values.value.length > 1);
const kind = computed(() => (mixed.value ? "mixed" : explicit.value.length > 0 ? "individual" : "default"));
const shown = computed(() => {
  if (mixed.value) return t("output.mixed", { count: values.value.length });
  const v = values.value[0];
  return v ? v : dir(state.value.defaultOutputDir);
});
const current = computed(() => (mixed.value ? null : (values.value[0] ?? null)));
</script>

<template>
  <div class="u-stack" data-testid="output-dir" :data-kind="kind">
    <div class="p-outdir">
      <v-icon :icon="mdiFolderOutline" size="small" aria-hidden="true" />
      <div class="p-outdir__text">
        <div class="text-body-medium font-weight-medium">{{ t("output.folder") }}<span class="font-weight-regular"> — {{ t(`output.kind.${kind}`) }}</span></div>
        <div class="text-body-medium p-break" data-testid="output-dir-value">{{ shown }}</div>
      </div>
    </div>
    <div class="u-row">
      <v-btn variant="outlined" :prepend-icon="mdiFolderEditOutline" :disabled="inputIds.length === 0" data-testid="output-dir-change" @click="picking = true">
        {{ inputIds.length > 1 ? t("output.changeMany", { count: inputIds.length }) : t("output.change") }}
      </v-btn>
      <v-btn v-if="explicit.length > 0" variant="text" :prepend-icon="mdiUndoVariant" data-testid="output-dir-reset" @click="demo.resetOutputDir(inputIds)">{{ t("output.reset") }}</v-btn>
    </div>
    <FolderPickerDialog v-model="picking" :current="current" @pick="(p) => demo.setOutputDir(inputIds, p)" />
  </div>
</template>
