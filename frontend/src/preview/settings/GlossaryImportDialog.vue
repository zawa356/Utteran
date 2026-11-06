<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { usePreview } from "../composables";
import { analyzeGlossary, decodeTxt, GLOSSARY_SAMPLES, mergeImport } from "../domain/glossaryText";
import { GLOSSARY_MAX } from "../domain/settingsSchema";

/**
 * "Import from TXT…" mock (#36). Instead of the OS file dialog it offers fixed SAMPLE files; no file
 * on the PC is read. It shows what the file holds (encoding, line endings, terms, problems) and
 * whether it replaces or extends the text. Importing only fills the text box — "Apply" still decides.
 */
const props = defineProps<{ draft: string }>();
const open = defineModel<boolean>({ required: true });
const emit = defineEmits<{ import: [text: string] }>();
const { t } = usePreview();
const sample = ref<string | null>(null);
const mode = ref<"replace" | "append">("replace");
watch(open, (v) => {
  if (!v) return;
  sample.value = null;
  mode.value = "replace";
});
const file = computed(() => GLOSSARY_SAMPLES.find((s) => s.name === sample.value) ?? null);
const decoded = computed(() => (file.value ? decodeTxt(file.value.content) : null));
const result = computed(() => (decoded.value ? analyzeGlossary(mergeImport(props.draft, decoded.value.text, mode.value)) : null));
const own = computed(() => (decoded.value ? analyzeGlossary(decoded.value.text) : null));
function confirm() {
  if (!decoded.value) return;
  emit("import", mergeImport(props.draft, decoded.value.text, mode.value));
  open.value = false;
}
</script>

<template>
  <v-dialog v-model="open" max-width="560" scrollable data-testid="glossary-import-dialog">
    <v-card variant="elevated" :title="t('glossary.importTitle')">
      <v-card-text class="u-stack">
        <p class="text-body-medium">{{ t("glossary.importNote") }}</p>
        <v-radio-group v-model="sample" hide-details :label="t('glossary.importFile')" data-testid="glossary-import-files">
          <v-radio v-for="s in GLOSSARY_SAMPLES" :key="s.name" :value="s.name" :label="s.name" color="primary" :data-testid="`glossary-sample-${GLOSSARY_SAMPLES.indexOf(s)}`" />
        </v-radio-group>
        <template v-if="decoded && own && result">
          <dl class="p-dl text-body-medium" data-testid="glossary-import-preview">
            <dt>{{ t("glossary.importEncoding") }}</dt>
            <dd>{{ decoded.bom ? t("glossary.utf8Bom") : t("glossary.utf8") }}</dd>
            <dt>{{ t("glossary.importNewline") }}</dt>
            <dd>{{ t(`glossary.newline.${decoded.newline}`) }}</dd>
            <dt>{{ t("glossary.importTerms") }}</dt>
            <dd data-testid="glossary-import-count">{{ t("glossary.importCount", { lines: own.lineCount, n: own.terms.length }) }}</dd>
          </dl>
          <v-radio-group v-model="mode" inline hide-details :label="t('glossary.importMode')" data-testid="glossary-import-mode">
            <v-radio value="replace" :label="t('glossary.importReplace')" color="primary" data-testid="glossary-import-replace" />
            <v-radio value="append" :label="t('glossary.importAppend')" color="primary" data-testid="glossary-import-append" />
          </v-radio-group>
          <p class="text-body-medium" data-testid="glossary-import-result" :class="{ 'text-error': result.tooMany || result.tooLongLines.length > 0 }">
            {{ result.tooMany || result.tooLongLines.length > 0 ? t("glossary.importBlocked", { n: result.terms.length, max: GLOSSARY_MAX }) : t("glossary.importAfter", { n: result.terms.length }) }}
          </p>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" data-testid="glossary-import-cancel" @click="open = false">{{ t("common.cancel") }}</v-btn>
        <v-btn color="primary" variant="tonal" :disabled="!decoded" data-testid="glossary-import-ok" @click="confirm">{{ t("glossary.importOk") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
