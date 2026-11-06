<script setup lang="ts">
import { mdiContentSaveOutline, mdiInformationOutline } from "@mdi/js";
import { ref } from "vue";
import { usePreview } from "../composables";

/**
 * The "save as" step every export shares (logs, diagnostics, presets): what is saved, the file
 * name and the format. "Save…" would open the OS save dialog in the product; the prototype says at
 * that exit, in one line, that it cannot save — and never pretends that a file was written.
 */
defineProps<{ target: string; fileName: string; formatLabel: string; testid: string }>();
defineEmits<{ back: [] }>();
const { t } = usePreview();
const tried = ref(false);
</script>

<template>
  <div class="u-stack" :data-testid="`${testid}-save`">
    <dl class="p-dl text-body-medium">
      <dt>{{ t("save.target") }}</dt>
      <dd :data-testid="`${testid}-save-target`">{{ target }}</dd>
      <dt>{{ t("save.fileName") }}</dt>
      <dd :data-testid="`${testid}-save-name`">{{ fileName }}</dd>
      <dt>{{ t("save.format") }}</dt>
      <dd>{{ formatLabel }}</dd>
    </dl>
    <slot />
    <div class="u-row">
      <v-btn variant="text" :data-testid="`${testid}-save-back`" @click="$emit('back')">{{ t("wizard.back") }}</v-btn>
      <v-spacer />
      <v-btn color="primary" :prepend-icon="mdiContentSaveOutline" :data-testid="`${testid}-save-button`" @click="tried = true">{{ t("save.save") }}</v-btn>
    </div>
    <p v-if="tried" class="p-feedback" role="status" :data-testid="`${testid}-save-unavailable`">
      <v-icon :icon="mdiInformationOutline" size="small" aria-hidden="true" />
      <span>{{ t("save.unavailable") }}</span>
    </p>
  </div>
</template>
