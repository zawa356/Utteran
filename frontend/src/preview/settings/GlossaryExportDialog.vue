<script setup lang="ts">
import { computed, ref } from "vue";
import SaveMock from "../dialogs/SaveMock.vue";
import { usePreview } from "../composables";
import { glossaryToTxt } from "../domain/glossaryText";

/**
 * "Export to TXT…" mock (#36): the APPLIED glossary as a UTF-8 text file, one term per line, with
 * the chosen line ending. The shared "save as" step says that nothing can be written.
 */
const props = defineProps<{ terms: readonly string[] }>();
const open = defineModel<boolean>({ required: true });
const { t } = usePreview();
const newline = ref<"lf" | "crlf">("crlf");
const preview = computed(() => glossaryToTxt(props.terms.slice(0, 8), "lf") + (props.terms.length > 8 ? "…" : ""));
const newlines = computed(() => [
  { value: "crlf", title: t("glossary.newline.crlf") },
  { value: "lf", title: t("glossary.newline.lf") },
]);
</script>

<template>
  <v-dialog v-model="open" max-width="520" scrollable data-testid="glossary-export-dialog">
    <v-card variant="elevated" :title="t('glossary.exportTitle')">
      <v-card-text class="u-stack">
        <v-select v-model="newline" :items="newlines" :label="t('glossary.importNewline')" data-testid="glossary-export-newline" />
        <pre class="p-log p-glossary__preview" data-testid="glossary-export-preview">{{ preview }}</pre>
        <SaveMock :target="t('glossary.exportTarget', { n: terms.length })" file-name="glossary.txt" :format-label="t(`glossary.exportFormat.${newline}`)" testid="glossary-export" @back="open = false" />
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
