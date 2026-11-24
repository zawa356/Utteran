<script setup lang="ts">
import { computed, ref } from "vue";
import { useCore, useI18n } from "../../composables/core";
import { analyzeGlossary, glossaryTxt, readGlossary } from "../../domain/v1/glossary";

const props = defineProps<{ terms: readonly string[]; disabled?: boolean }>();
const emit = defineEmits<{ apply: [terms: string[]]; dirty: [dirty: boolean] }>();
const { t } = useI18n();
const { host } = useCore();
const embedded = host.blocksDownloads;
const text = ref(props.terms.join("\n"));
const analysis = computed(() => analyzeGlossary(text.value));
const preview = ref<{ append: string; replace: string } | null>(null);
const mode = ref<"append" | "replace">("replace");
const proposed = computed(() => preview.value?.[mode.value] ?? "");
const importAnalysis = computed(() => analyzeGlossary(proposed.value));
const report = computed(() => preview.value ? importAnalysis.value : analysis.value);
const reading = ref(false);
const fileError = ref(false);
const newline = ref<"LF" | "CRLF">("LF");
const composing = ref(false);
const exportRequested = ref(false);

function edit(value: string) {
  text.value = value;
  emit("dirty", value !== props.terms.join("\n") || preview.value !== null || reading.value);
}

async function importFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || reading.value) return;
  reading.value = true;
  preview.value = null;
  fileError.value = false;
  edit(text.value);
  try {
    const imported = await readGlossary(file);
    preview.value = { replace: imported, append: [text.value, imported].filter(Boolean).join("\n") };
  } catch {
    fileError.value = true;
  } finally {
    reading.value = false;
    edit(text.value);
  }
}

function acceptImport() {
  if (!preview.value || !importAnalysis.value.valid) return;
  const value = proposed.value;
  preview.value = null;
  edit(value);
}

function apply() {
  if (props.disabled || composing.value || reading.value || preview.value || !analysis.value.valid) return;
  const terms = analysis.value.terms;
  text.value = terms.join("\n");
  emit("apply", terms);
  emit("dirty", false);
}

function download() {
  if (host.blocksDownloads) return;
  const url = URL.createObjectURL(glossaryTxt(props.terms, newline.value));
  const link = document.createElement("a");
  link.href = url;
  link.download = "glossary.txt";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  exportRequested.value = true;
}
</script>

<template>
  <section class="u-stack">
    <v-textarea
      :model-value="text" :label="t('ea.glossary')" :hint="t('ea.glossaryHint')" persistent-hint
      rows="5" autocomplete="off" spellcheck="false" :disabled="disabled || reading || preview !== null"
      data-testid="ea-glossary" @update:model-value="edit($event ?? '')"
      @compositionstart="composing = true" @compositionend="composing = false"
    />
    <div class="text-caption u-break" role="status" data-testid="ea-glossary-report">
      {{ t("ea.termCount", { count: report.terms.length }) }}
      <p v-if="report.empty.length">{{ t("ea.empty", { lines: report.empty.slice(0, 20).join(", "), count: report.empty.length }) }}</p>
      <p v-if="report.trimmed.length">{{ t("ea.trimmed", { lines: report.trimmed.slice(0, 20).join(", "), count: report.trimmed.length }) }}</p>
      <p v-if="report.duplicates.length">{{ t("ea.duplicates", { lines: report.duplicates.slice(0, 20).map(d => `${d.line} → ${d.first}`).join(", "), count: report.duplicates.length }) }}</p>
      <p v-if="report.long.length">{{ t("ea.long", { lines: report.long.slice(0, 20).join(", "), count: report.long.length }) }}</p>
      <p v-if="report.invalid.length">{{ t("ea.invalid", { lines: report.invalid.slice(0, 20).join(", "), count: report.invalid.length }) }}</p>
      <p v-if="report.overflow">{{ t("ea.overflow") }}</p>
    </div>
    <div class="u-row">
      <v-btn :disabled="disabled || composing || reading || preview !== null || !analysis.valid" data-testid="ea-glossary-apply" @click="apply">{{ t("ea.apply") }}</v-btn>
      <label class="text-body-2">
        {{ t("ea.import") }}
        <input type="file" accept=".txt,text/plain" :disabled="disabled || reading" data-testid="ea-glossary-import" @change="importFile" />
      </label>
    </div>
    <v-alert v-if="fileError" type="error" density="compact">{{ t("ea.fileError") }}</v-alert>
    <template v-if="preview">
      <v-select v-model="mode" :items="[{ value: 'replace', title: t('ea.replace') }, { value: 'append', title: t('ea.append') }]" :label="t('ea.importMode')" />
      <v-textarea :model-value="proposed" :label="t('ea.preview')" readonly rows="3" />
      <div class="u-row">
        <v-btn :disabled="disabled || !importAnalysis.valid" data-testid="ea-import-accept" @click="acceptImport">{{ t("ea.importDraft") }}</v-btn>
        <v-btn variant="text" @click="preview = null; edit(text)">{{ t("v1.create.back") }}</v-btn>
      </div>
    </template>
    <div v-if="!embedded" class="u-row">
      <v-select v-model="newline" :items="['LF', 'CRLF']" :label="t('ea.newline')" density="compact" hide-details />
      <v-btn :disabled="disabled" data-testid="ea-glossary-export" @click="download">{{ t("ea.export") }}</v-btn>
    </div>
    <p v-if="embedded" class="text-caption">{{ t("ea.embeddedExport") }}</p>
    <p v-if="exportRequested" class="text-caption" role="status">{{ t("ea.exportRequested") }}</p>
  </section>
</template>

<style scoped>
input[type="file"] {
  display: block;
  max-width: 100%;
}
</style>
