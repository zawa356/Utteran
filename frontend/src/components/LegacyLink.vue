<script setup lang="ts">
import { computed, ref } from "vue";
import { useCore, useI18n } from "../composables/core";
import { useStore } from "../composables/useStore";
import { isDirty } from "../domain/settings";

/**
 * Go to the legacy screen (`/`) in the same session. With unsaved settings the person chooses
 * save-and-go, go-without-saving or cancel; nothing is saved or started implicitly.
 */
const props = withDefaults(
  defineProps<{ label: string; testid: string; variant?: "flat" | "text" | "outlined" | "tonal"; size?: "small" | "default" }>(),
  { variant: "text", size: "default" },
);
const { settings } = useCore();
const state = useStore(settings.store);
const { t } = useI18n();
const asking = ref(false);
const saveFailed = ref(false);
const saving = computed(() => state.value.save.phase === "pending");

function go(): void {
  window.location.assign("/");
}
function onClick(): void {
  if (!isDirty(state.value)) return go();
  saveFailed.value = false;
  asking.value = true;
}
async function saveAndGo(): Promise<void> {
  saveFailed.value = false;
  if (await settings.save()) return go();
  saveFailed.value = true;
}
</script>

<template>
  <v-btn :variant="props.variant" :size="props.size" :data-testid="props.testid" @click="onClick">{{ props.label }}</v-btn>
  <v-dialog v-model="asking" max-width="440" :persistent="saving" data-testid="leave-dialog">
    <v-card :title="t('leave.title')" data-testid="leave-dialog-card">
      <v-card-text>
        <p>{{ t("leave.body") }}</p>
        <p v-if="saveFailed" role="alert" class="text-error mt-2" data-testid="leave-save-failed">{{ t("leave.saveFailed") }}</p>
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-btn color="primary" variant="flat" :loading="saving" data-testid="leave-save" @click="saveAndGo">{{ t("leave.save") }}</v-btn>
        <v-btn variant="outlined" :disabled="saving" data-testid="leave-discard" @click="go">{{ t("leave.discard") }}</v-btn>
        <v-btn variant="text" :disabled="saving" data-testid="leave-cancel" @click="asking = false">{{ t("common.cancel") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
