<script setup lang="ts">
import { computed, onMounted, onUnmounted } from "vue";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { MAX_INPUTS } from "../../domain/inputCollection";
import { checkPath, MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import type { MessageKey } from "../../i18n";

/**
 * Adding inputs to the shared list (S3-B1): the native dialog (one file per call, always added),
 * a native drop (several files at once) or a typed absolute path with an explicit "Add". Paths
 * are shown here only; never stored in browser storage, put in a URL, logged or reported.
 */
const { core, list } = useTranscription();
const { t } = useI18n();

let unsubscribe: (() => void) | null = null;
onMounted(() => {
  // Native drops reach the list only while an input area is on screen.
  unsubscribe = core.host.onDroppedPaths((paths) => core.inputs.acceptDrop(paths));
});
onUnmounted(() => {
  unsubscribe?.();
  unsubscribe = null;
});

// In a plain browser a dropped file would replace the page; refuse it here (no upload exists).
function refuseBrowserDrop(event: DragEvent) {
  event.preventDefault();
}

const typedProblem = computed(() => {
  const typed = list.value.typed;
  if (typed === "") return [];
  const problem = checkPath(typed, "input_file");
  return problem ? [t(`tx.path.${problem}` as MessageKey)] : [];
});
const full = computed(() => list.value.rows.length >= MAX_INPUTS);

const notice = computed(() => {
  const n = list.value.notice;
  if (!n) return null;
  switch (n.kind) {
    case "added":
      return { type: "success" as const, text: t("tx.list.notice.added", { count: n.count }) };
    case "duplicate":
      return { type: "info" as const, text: t("tx.list.notice.duplicate") };
    case "invalid":
      return { type: "warning" as const, text: `${t("tx.list.notice.invalid")} ${t(`tx.path.${n.problem}` as MessageKey)}` };
    case "limit":
      return { type: "warning" as const, text: t("tx.list.notice.limit", { max: n.max, requested: n.requested, room: n.room }) };
    case "drop":
      return { type: "warning" as const, text: t("tx.list.notice.drop", { added: n.added, duplicates: n.duplicates, folders: n.folders, unknown: n.unknown, invalid: n.invalid }) };
    case "removed":
      return { type: "info" as const, text: n.kept > 0 ? t("tx.list.notice.removedKept", { removed: n.removed, kept: n.kept }) : t("tx.list.notice.removed", { removed: n.removed }) };
  }
  return null;
});

function add() {
  core.inputs.addTyped();
}
</script>

<template>
  <div class="u-stack" data-testid="tx-input-add" @dragover="refuseBrowserDrop" @drop="refuseBrowserDrop">
    <div class="u-row">
      <v-btn variant="outlined" :loading="list.picking" :disabled="list.picking || full" data-testid="tx-input-pick" @click="core.inputs.pick(core.host)">
        {{ t("tx.input.pick") }}
      </v-btn>
    </div>
    <div class="u-row u-row--field">
      <v-text-field
        :label="t('tx.input.pathLabel')"
        :model-value="list.typed"
        :hint="t('tx.input.pathHint')"
        persistent-hint
        :maxlength="MAX_JOB_PATH_LENGTH + 1"
        :error-messages="typedProblem"
        autocomplete="off"
        spellcheck="false"
        data-testid="tx-input-path"
        @update:model-value="(v: string) => core.inputs.setTyped(v ?? '')"
        @keydown.enter.prevent="add"
      />
      <v-btn variant="tonal" :disabled="list.typed === '' || typedProblem.length > 0 || full" data-testid="tx-input-add-typed" @click="add">{{ t("tx.input.add") }}</v-btn>
    </div>
    <p v-if="list.picking" role="status" class="text-body-2">{{ t("tx.input.picking") }}</p>
    <p class="text-caption" data-testid="tx-drop-hint">{{ t("tx.input.dropHint") }}</p>
    <p class="text-caption" data-testid="tx-input-limit">{{ t("tx.input.limit", { max: MAX_INPUTS }) }}</p>
    <v-alert v-if="notice" :type="notice.type" density="compact" role="status" data-testid="tx-list-notice">{{ notice.text }}</v-alert>
    <v-alert v-if="list.pickError" type="info" role="status" data-testid="tx-input-pick-error">
      {{ list.pickError === "host_unavailable" ? t("tx.pick.hostUnavailable") : t("tx.pick.failed") }}
    </v-alert>
  </div>
</template>
