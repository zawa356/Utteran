<script setup lang="ts">
import { mdiCheck, mdiExport, mdiImport, mdiUndoVariant } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { glossaryDraftKey } from "../domain/demo";
import { analyzeGlossary, canApply, glossaryToText } from "../domain/glossaryText";
import { GLOSSARY_ITEM_MAX, GLOSSARY_MAX } from "../domain/settingsSchema";
import GlossaryExportDialog from "./GlossaryExportDialog.vue";
import GlossaryImportDialog from "./GlossaryImportDialog.vue";
import { useSettingAccess } from "./useSettingAccess";

/**
 * Glossary (asr.glossary), iteration 5 (owner memo #36): ONE multi-line text box, one term per line,
 * so long lists can be pasted at once. Typing never changes the stored glossary — "Apply" does, and
 * the summary says beforehand what it will skip (empty lines, repeated terms) or refuse (too long,
 * more than 1000). Enter is just a new line, also during IME conversion; nothing is applied on a key.
 * The draft is kept per editing target (category / model changes do not lose it).
 * Several selected files with DIFFERENT glossaries start with an empty box: nothing of one file is
 * shown as if it belonged to the others, and "Apply to all n selected files" says what will happen.
 */
defineProps<{ disabled: boolean }>();
const access = useSettingAccess();
const { demo, state, t } = usePreview();
const key = computed(() => glossaryDraftKey(access.mode, access.targetIds.value));
const mixed = computed(() => access.mixed("glossary"));
const stored = computed(() => (mixed.value ? null : access.value("glossary")));
const storedText = computed(() => (stored.value ? glossaryToText(stored.value) : ""));
const draft = computed(() => state.value.glossaryDrafts[key.value]);
const text = computed(() => draft.value ?? storedText.value);
const dirty = computed(() => draft.value !== undefined && draft.value !== storedText.value);
const a = computed(() => analyzeGlossary(text.value));
const many = computed(() => access.mode === "selection" && access.targetIds.value.length > 1);
const importing = ref(false);
const exporting = ref(false);

function onInput(v: string) {
  demo.setGlossaryDraft(key.value, v === storedText.value ? null : v);
}
function apply() {
  if (!dirty.value || !canApply(a.value)) return;
  access.set("glossary", [...a.value.terms]);
  demo.setGlossaryDraft(key.value, null);
}
function discard() {
  demo.setGlossaryDraft(key.value, null);
}
const lines = (nums: number[]) => (nums.length > 5 ? `${nums.slice(0, 5).join(", ")}…` : nums.join(", "));
</script>

<template>
  <div class="u-stack p-glossary" data-testid="glossary" :data-dirty="dirty">
    <p v-if="mixed" class="text-body-medium" data-testid="glossary-mixed">{{ t("glossary.mixed", { count: access.targetIds.value.length }) }}</p>
    <label :for="`p-glossary-${access.mode}`" class="text-body-medium">{{ t("glossary.text") }}</label>
    <v-textarea
      :id="`p-glossary-${access.mode}`"
      :model-value="text"
      :disabled="disabled"
      :placeholder="t('glossary.placeholder')"
      rows="8"
      max-rows="16"
      auto-grow
      spellcheck="false"
      hide-details
      class="p-glossary__text"
      data-testid="glossary-text"
      @update:model-value="onInput"
    />
    <div class="text-body-medium" data-testid="glossary-count">{{ t("glossary.summary", { lines: a.lineCount, n: a.terms.length, max: GLOSSARY_MAX }) }}</div>
    <ul v-if="a.emptyLines + a.duplicateLines.length + a.trimmedLines + a.tooLongLines.length > 0 || a.tooMany" class="p-glossary__issues text-body-medium" data-testid="glossary-issues">
      <li v-if="a.tooLongLines.length > 0" class="text-error" data-testid="glossary-too-long">{{ t("glossary.tooLong", { lines: lines(a.tooLongLines), max: GLOSSARY_ITEM_MAX }) }}</li>
      <li v-if="a.tooMany" class="text-error" data-testid="glossary-too-many">{{ t("glossary.tooMany", { n: a.terms.length, max: GLOSSARY_MAX }) }}</li>
      <li v-if="a.duplicateLines.length > 0" data-testid="glossary-duplicates">{{ t("glossary.duplicates", { count: a.duplicateLines.length, lines: lines(a.duplicateLines) }) }}</li>
      <li v-if="a.emptyLines > 0" data-testid="glossary-empty-lines">{{ t("glossary.emptyLines", { count: a.emptyLines }) }}</li>
      <li v-if="a.trimmedLines > 0" data-testid="glossary-trimmed">{{ t("glossary.trimmed", { count: a.trimmedLines }) }}</li>
    </ul>
    <div class="u-row">
      <v-btn color="primary" variant="tonal" :prepend-icon="mdiCheck" :disabled="disabled || !dirty || !canApply(a)" data-testid="glossary-apply" @click="apply">
        {{ many ? t("glossary.applyAll", { count: access.targetIds.value.length }) : t("glossary.apply") }}
      </v-btn>
      <v-btn v-if="dirty" variant="text" :prepend-icon="mdiUndoVariant" data-testid="glossary-discard" @click="discard">{{ t("glossary.discard") }}</v-btn>
      <v-spacer />
      <v-btn variant="text" :prepend-icon="mdiImport" :disabled="disabled" data-testid="glossary-import" @click="importing = true">{{ t("glossary.import") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiExport" :disabled="mixed || (stored?.length ?? 0) === 0" data-testid="glossary-export" @click="exporting = true">{{ t("glossary.export") }}</v-btn>
    </div>
    <p v-if="dirty" class="text-body-medium" role="status" data-testid="glossary-unapplied">{{ t("glossary.unapplied") }}</p>
    <GlossaryImportDialog v-model="importing" :draft="text" @import="(v) => onInput(v)" />
    <GlossaryExportDialog v-model="exporting" :terms="stored ?? []" />
  </div>
</template>
