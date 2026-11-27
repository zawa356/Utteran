<script setup lang="ts">
import { mdiCheck } from "@mdi/js";
import { computed } from "vue";
import { OUTPUT_FORMATS_V1, type OutputFormatV1 } from "../../api/v1Resources";
import { useCodeText, useV1 } from "../../composables/v1";
import { summarize, V1_LANGUAGES } from "../../domain/v1/settings";
import type { MessageKey } from "../../i18n";

export type BasicField = "language" | "diarization" | "formats" | "model";

/**
 * The everyday settings of the Design Freeze (#96): language, diarization and output formats
 * (model behind "details"). Language and formats are the screen's differences from the selected
 * Preset (B5 `common_overrides`, `/asr/language` and `/output/formats` — the only common leaves the
 * v1 path supports). Diarization and the model are shown as they ARE: the v1 worker runs without
 * diarization and the model comes from the Preset revision, so neither is offered as an editable
 * control here (no dummy switch).
 */
const props = withDefaults(defineProps<{ fields?: readonly BasicField[] }>(), { fields: () => ["language", "diarization", "formats"] });
const { v1, library } = useV1();
const { t } = useCodeText();
const has = (f: BasicField) => props.fields.includes(f);

const preset = computed(() => library.value.preset.item);
const presetSummary = computed(() => (preset.value ? summarize(preset.value.settings) : null));
const screen = computed(() => library.value.screen);
const languageItems = computed(() => [
  { title: t("v1.diff.inherit", { value: presetSummary.value ? langText(presetSummary.value.language) : "" }), value: null },
  ...V1_LANGUAGES.map((l) => ({ title: langText(l), value: l })),
]);
const effectiveFormats = computed<readonly string[]>(() => screen.value.formats ?? presetSummary.value?.formats ?? []);
const diffCount = computed(() => (preset.value ? (screen.value.language !== null && screen.value.language !== presetSummary.value?.language ? 1 : 0) + (screen.value.formats !== null && screen.value.formats.join() !== presetSummary.value?.formats.join() ? 1 : 0) : 0));

function langText(code: string): string {
  const key = `tx.lang.${code}`;
  return (V1_LANGUAGES as readonly string[]).includes(code) ? t(key as MessageKey) : code;
}
function toggleFormat(f: OutputFormatV1, on: boolean) {
  const base = [...effectiveFormats.value] as OutputFormatV1[];
  const next = on ? [...new Set([...base, f])] : base.filter((x) => x !== f);
  v1.library.setFormats(OUTPUT_FORMATS_V1.filter((x) => next.includes(x)));
}
</script>

<template>
  <section class="u-fields-wrap" :aria-label="t('ui3.settings.basic')" data-testid="v1-basic-settings" :data-fields="fields.join(' ')">
    <p v-if="!preset && (has('language') || has('formats'))" class="text-body-2" data-testid="v1-basic-no-preset">{{ t("ui3.settings.needPreset") }}</p>
    <div class="u-fields">
      <v-select
        v-if="has('language')"
        :label="t('tx.options.language')"
        :items="languageItems"
        :model-value="screen.language"
        :disabled="!preset"
        hide-details
        data-testid="v1-diff-language"
        @update:model-value="(v: string | null) => v1.library.setLanguage(v)"
      />
      <div v-if="has('diarization')" class="u-field" data-testid="v1-basic-diarization">
        <span class="u-field__label">{{ t("ui3.settings.diarization") }}</span>
        <span class="text-body-2" data-testid="v1-basic-diarization-value">{{ t("ui3.settings.diarizationUnsupported") }}</span>
      </div>
      <fieldset v-if="has('formats')" class="u-fieldset u-field u-field--wide" data-testid="v1-diff-formats">
        <legend class="u-field__label">{{ t("tx.options.formats") }}</legend>
        <div class="u-chips" role="group" :aria-label="t('tx.options.formats')">
          <v-chip
            v-for="f in OUTPUT_FORMATS_V1"
            :key="f"
            label
            variant="outlined"
            :color="effectiveFormats.includes(f) ? 'primary' : undefined"
            :prepend-icon="effectiveFormats.includes(f) ? mdiCheck : undefined"
            :disabled="!preset"
            :aria-pressed="effectiveFormats.includes(f)"
            :data-testid="`v1-diff-format-${f}`"
            :data-selected="effectiveFormats.includes(f)"
            @click="toggleFormat(f, !effectiveFormats.includes(f))"
          >
            {{ f.toUpperCase() }}
          </v-chip>
        </div>
        <p v-if="preset && !effectiveFormats.includes('json')" class="text-caption" role="status" data-testid="v1-diff-no-json">{{ t("ec.noJsonWarning") }}</p>
        <v-btn v-if="screen.formats !== null" variant="text" size="small" data-testid="v1-diff-formats-reset" @click="v1.library.setFormats(null)">{{ t("v1.diff.reset") }}</v-btn>
      </fieldset>
      <div v-if="has('model')" class="u-field u-field--wide" data-testid="v1-basic-model">
        <span class="u-field__label">{{ t("tx.options.model") }}</span>
        <span class="text-body-2 u-break">{{ presetSummary ? t("ui3.settings.modelFromPreset", { model: presetSummary.model, backend: presetSummary.backend }) : t("ui3.settings.needPreset") }}</span>
        <span class="text-caption">{{ t("ui3.settings.modelHow") }}</span>
      </div>
    </div>
    <p v-if="preset && (has('language') || has('formats'))" class="text-caption mt-2" data-testid="v1-diff-count">{{ t("v1.diff.explain", { count: diffCount }) }}</p>
  </section>
</template>
