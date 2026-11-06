<script setup lang="ts">
import { mdiFolderOutline } from "@mdi/js";
import { ref, watch } from "vue";
import { usePreview } from "../composables";
import { SAMPLE_FOLDERS } from "../domain/demo";

/**
 * Stand-in for the OS folder picker (#35). It lists fixed SAMPLE folders; nothing is read, listed,
 * created or checked on the PC. The product opens the OS dialog and validates the result with the
 * backend (absolute path, exists, writable, symlink / network-path rules — B7).
 */
const props = defineProps<{ current: string | null }>();
const open = defineModel<boolean>({ required: true });
const emit = defineEmits<{ pick: [path: string] }>();
const { t } = usePreview();
const choice = ref<string | null>(null);
watch(open, (v) => v && (choice.value = props.current));
function confirm() {
  if (!choice.value) return;
  emit("pick", choice.value);
  open.value = false;
}
</script>

<template>
  <v-dialog v-model="open" max-width="480" data-testid="folder-picker">
    <v-card variant="elevated" :title="t('output.pickTitle')" data-testid="folder-picker-card">
      <v-card-text class="u-stack">
        <p class="text-body-medium" data-testid="folder-picker-note">{{ t("output.pickNote") }}</p>
        <v-radio-group v-model="choice" hide-details :aria-label="t('output.pickTitle')" data-testid="folder-picker-list">
          <v-radio v-for="f in SAMPLE_FOLDERS" :key="f" :value="f" color="primary" :data-testid="`folder-option-${SAMPLE_FOLDERS.indexOf(f)}`">
            <template #label><v-icon :icon="mdiFolderOutline" size="small" class="mr-1" aria-hidden="true" /><span class="p-break">{{ f }}</span></template>
          </v-radio>
        </v-radio-group>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" data-testid="folder-picker-cancel" @click="open = false">{{ t("common.cancel") }}</v-btn>
        <v-btn color="primary" variant="tonal" :disabled="!choice" data-testid="folder-picker-ok" @click="confirm">{{ t("output.pickOk") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
