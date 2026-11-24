<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { JsonObject } from "../../api/v1Resources";
import { useStore } from "../../composables/useStore";
import { useV1 } from "../../composables/v1";
import { useI18n } from "../../composables/core";
import { canonical } from "../../domain/v1/settings";
import GlossaryEditor from "./GlossaryEditor.vue";

const { core, library } = useV1();
const { t } = useI18n();
const list = useStore(core.inputs.store);
const rows = computed(() => {
  void list.value;
  return core.inputs.editableSelection();
});
const preset = computed(() => library.value.preset.item);
const open = ref(false);
const dirty = ref(false);
const discard = ref(false);
const changed = ref(false);
const mixed = ref(false);
const terms = ref<readonly string[]>([]);
let target: string[] = [];

function start() {
  const common = (preset.value?.settings.asr as JsonObject | undefined)?.glossary as string[] | undefined;
  const values = rows.value.map(row => row.overrides.glossary ?? common ?? []);
  const first = values[0] ?? [];
  mixed.value = values.some(value => canonical(value) !== canonical(first));
  terms.value = mixed.value ? [] : first;
  target = rows.value.map(row => row.clientFileId);
  dirty.value = false;
  discard.value = false;
  changed.value = false;
  open.value = true;
}

function close() {
  if (discard.value) discard.value = false;
  else if (dirty.value) discard.value = true;
  else open.value = false;
}

function apply(next: readonly string[] | null) {
  if (core.inputs.setGlossary(target, next)) open.value = false;
  else changed.value = true;
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!open.value || !dirty.value) return;
  event.preventDefault();
  event.returnValue = "";
}
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onUnmounted(() => window.removeEventListener("beforeunload", beforeUnload));
</script>

<template>
  <v-dialog :model-value="open" persistent scrollable max-width="720" @keydown.esc.prevent.stop="(event: KeyboardEvent) => { if (!event.isComposing) close(); }">
    <template #activator="{ props }">
      <v-btn v-bind="props" :disabled="!rows.length || !preset" variant="outlined" size="small" data-testid="ea-row-glossary" @click="start">{{ t("ea.rowGlossary") }}</v-btn>
    </template>
    <v-card :title="t('ea.rowGlossary')">
      <v-card-text class="u-stack">
        <p>{{ t("ea.rowReplace", { count: target.length }) }}</p>
        <v-alert v-if="mixed" type="info" density="compact">{{ t("tx.ov.mixed", { count: target.length }) }}</v-alert>
        <v-alert v-if="changed" type="error" density="compact">{{ t("ea.rowChanged") }}</v-alert>
        <GlossaryEditor v-if="open" :terms="terms" :disabled="discard || changed" @apply="apply" @dirty="dirty = $event || changed" />
        <v-alert v-if="discard" type="warning" density="compact">{{ t("ea.confirm.close") }}</v-alert>
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <template v-if="discard">
          <v-btn autofocus @click="discard = false">{{ t("v1.create.back") }}</v-btn>
          <v-btn @click="open = false">{{ t("ea.close") }}</v-btn>
        </template>
        <template v-else>
          <v-btn @click="close">{{ t("ea.close") }}</v-btn>
          <v-btn :disabled="dirty || changed" data-testid="ea-row-glossary-inherit" @click="apply(null)">{{ t("ea.rowInherit", { count: target.length }) }}</v-btn>
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
