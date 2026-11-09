<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import { MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import type { MessageKey } from "../../i18n";

/**
 * S3-A input: exactly one local file, chosen with the native dialog, dropped onto the window
 * (pywebview native drop, via HostBridge) or typed. The path is shown here only; it is never
 * stored in browser storage, put in a URL, logged or reported.
 */
const { core, form, issue } = useTranscription();
const { t } = useI18n();

let unsubscribe: (() => void) | null = null;
onMounted(() => {
  // Native drops reach the form only while an input area is on screen.
  unsubscribe = core.host.onDroppedPaths((paths) => core.transcription.acceptDrop(paths));
});
onUnmounted(() => {
  unsubscribe?.();
  unsubscribe = null;
});

// In a plain browser a dropped file would replace the page; refuse it here (no upload exists).
function refuseBrowserDrop(event: DragEvent) {
  event.preventDefault();
}

function inputProblem(): string[] {
  const i = issue("input");
  if (!i || form.value.draft.inputPath === "") return [];
  return [t(`tx.path.${i.problem}` as MessageKey)];
}
function dropNotice(): string | null {
  const n = form.value.dropNotice;
  if (!n) return null;
  return n.kind === "multiple" ? t("tx.drop.multiple", { count: n.count }) : t(`tx.drop.${n.kind}` as MessageKey);
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-input-heading" data-testid="tx-input" @dragover="refuseBrowserDrop" @drop="refuseBrowserDrop">
    <h2 id="tx-input-heading" class="u-subheading">{{ t("tx.input.title") }}</h2>
    <p class="text-body-2">{{ t("tx.input.oneOnly") }}</p>
    <div class="u-row">
      <v-btn
        variant="outlined"
        :loading="form.picking === 'input_file'"
        :disabled="form.picking !== null"
        data-testid="tx-input-pick"
        @click="core.transcription.pick('input_file', core.host)"
      >
        {{ t("tx.input.pick") }}
      </v-btn>
      <v-btn variant="text" :disabled="form.draft.inputPath === '' || form.picking !== null" data-testid="tx-input-clear" @click="core.transcription.clearInput()">
        {{ t("tx.input.clear") }}
      </v-btn>
    </div>
    <v-text-field
      :label="t('tx.input.pathLabel')"
      :model-value="form.draft.inputPath"
      :readonly="form.picking === 'input_file'"
      :hint="t('tx.input.pathHint')"
      persistent-hint
      :maxlength="MAX_JOB_PATH_LENGTH + 1"
      :error-messages="inputProblem()"
      autocomplete="off"
      spellcheck="false"
      data-testid="tx-input-path"
      @update:model-value="(v: string) => core.transcription.edit({ inputPath: v ?? '' })"
    />
    <p v-if="form.draft.inputSource" class="text-caption" data-testid="tx-input-source">{{ t(`tx.input.source.${form.draft.inputSource}` as MessageKey) }}</p>
    <p v-if="form.picking === 'input_file'" role="status" class="text-body-2">{{ t("tx.input.picking") }}</p>
    <p class="text-caption" data-testid="tx-drop-hint">{{ t("tx.input.dropHint") }}</p>
    <v-alert v-if="dropNotice()" type="warning" role="status" data-testid="tx-drop-notice">{{ dropNotice() }}</v-alert>
    <v-alert v-if="form.pickError?.target === 'input_file'" type="info" role="status" data-testid="tx-input-pick-error">
      {{ form.pickError.reason === "host_unavailable" ? t("tx.pick.hostUnavailable") : t("tx.pick.failed") }}
    </v-alert>
  </section>
</template>
