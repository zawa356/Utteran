<script setup lang="ts">
import { mdiDeleteOutline, mdiPlus } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { GLOSSARY_ITEM_MAX, GLOSSARY_MAX, glossaryEntryIssue } from "../domain/settingsSchema";
import type { PreviewKey } from "../i18n";
import { useSettingAccess } from "./useSettingAccess";

/**
 * Glossary (asr.glossary): one term per row — add, edit in place, delete. Enter adds the new term,
 * except while an IME composition is open (that Enter only confirms the conversion). Each row is
 * checked for empty / too long / duplicate with the same rules as the validator.
 */
defineProps<{ disabled: boolean }>();
const access = useSettingAccess();
const { t } = usePreview();
const list = computed(() => (access.mixed("glossary") ? [] : access.value("glossary")));
const draft = ref("");
const composing = ref(false);
const draftIssue = computed(() => (draft.value === "" ? null : glossaryEntryIssue(draft.value, list.value)));
const canAdd = computed(() => draft.value.trim() !== "" && draftIssue.value === null && list.value.length < GLOSSARY_MAX);

function add() {
  if (!canAdd.value) return;
  access.set("glossary", [...list.value, draft.value.trim()]);
  draft.value = "";
}
function onKey(e: KeyboardEvent) {
  if (e.key !== "Enter" || e.isComposing || composing.value || e.keyCode === 229) return;
  e.preventDefault();
  add();
}
function edit(i: number, v: string) {
  if (composing.value) return;
  const next = [...list.value];
  next[i] = v;
  access.set("glossary", next);
}
function remove(i: number) {
  access.set("glossary", list.value.filter((_, j) => j !== i));
}
const issueText = (code: string | null) => (code ? t(`issue.${code}` as PreviewKey, { max: GLOSSARY_ITEM_MAX }) : undefined);
</script>

<template>
  <div class="u-stack p-glossary" data-testid="glossary" @compositionstart="composing = true" @compositionend="composing = false">
    <div class="text-body-medium" data-testid="glossary-count">{{ t("glossary.count", { n: list.length, max: GLOSSARY_MAX }) }}</div>
    <p v-if="access.mixed('glossary')" class="text-body-medium">{{ t("inspector.mixed") }}</p>
    <ul v-else-if="list.length > 0" class="p-glossary__list" :aria-label="t('field.glossary')">
      <li v-for="(term, i) in list" :key="i" :data-testid="`glossary-item-${i}`">
        <v-text-field
          :model-value="term"
          :disabled="disabled"
          :aria-label="t('glossary.itemLabel', { n: i + 1 })"
          :error-messages="access.issueText('glossary', i)"
          hide-details="auto"
          :data-testid="`glossary-edit-${i}`"
          @update:model-value="(v) => edit(i, v)"
        />
        <v-btn variant="text" :icon="mdiDeleteOutline" :disabled="disabled" :aria-label="t('glossary.remove', { term })" :title="t('glossary.removeShort')" :data-testid="`glossary-remove-${i}`" @click="remove(i)" />
      </li>
    </ul>
    <p v-else class="text-body-medium" data-testid="glossary-empty">{{ t("glossary.empty") }}</p>
    <div class="p-glossary__add">
      <v-text-field
        v-model="draft"
        :label="t('glossary.new')"
        :disabled="disabled || access.mixed('glossary')"
        :error-messages="issueText(draftIssue)"
        hide-details="auto"
        data-testid="glossary-new"
        @keydown="onKey"
      />
      <v-btn variant="tonal" color="primary" :prepend-icon="mdiPlus" :disabled="disabled || !canAdd" data-testid="glossary-add" @click="add">{{ t("glossary.add") }}</v-btn>
    </div>
  </div>
</template>
