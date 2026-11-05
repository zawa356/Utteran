<script setup lang="ts">
import { mdiClose } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import AboutView from "../dialogs/AboutView.vue";
import DiagnosticsView from "../dialogs/DiagnosticsView.vue";
import LogView from "../dialogs/LogView.vue";
import type { PreviewKey } from "../i18n";

/**
 * Dialogs opened from the app menu (Tools / Help) and the setup wizard. Mock only: the logs are
 * fixed samples, exports end at a "save as" step that writes nothing, nothing is read from disk or
 * the backend. Iteration 4: no "demo" chips in the dialogs — the one-line banner says it once.
 */
const { layout, layoutState, t } = usePreview();
const open = computed({
  get: () => layoutState.value.dialog !== null,
  set: (v: boolean) => !v && layout.openDialog(null),
});
const dialog = computed(() => layoutState.value.dialog);
const close = () => layout.openDialog(null);
</script>

<template>
  <v-dialog v-model="open" max-width="720" scrollable data-testid="app-dialog">
    <v-card v-if="dialog" variant="elevated" :data-testid="`dialog-${dialog}`">
      <v-card-title class="d-flex align-center ga-2">
        <span class="p-card__title">{{ t(`dialog.${dialog}.title` as PreviewKey) }}</span>
        <v-spacer />
        <v-btn variant="text" :icon="mdiClose" size="small" :aria-label="t('common.close')" data-testid="dialog-close" @click="close" />
      </v-card-title>
      <v-card-text>
        <LogView v-if="dialog === 'systemLog' || dialog === 'jobLog' || dialog === 'setupLog'" :key="dialog" :kind="dialog" />
        <DiagnosticsView v-else-if="dialog === 'diagnostics'" />
        <AboutView v-else />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" data-testid="dialog-ok" @click="close">{{ t("common.close") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
