<script setup lang="ts">
import { computed, ref } from "vue";
import { OUTPUT_FORMATS_V1, type OutputFormatV1, type PolicyV1, type PresetV1 } from "../../api/v1Resources";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCodeText, useV1 } from "../../composables/v1";
import { MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import { DEFAULT_PRESET_MODEL, type PresetDraft, presetDraftIssues, presetSettings, summarize, V1_LANGUAGES } from "../../domain/v1/settings";
import type { MessageKey } from "../../i18n";

/**
 * S5-A (`--next-ui-v1`): the saved Preset/Policy revisions this start uses, the screen's
 * differences (B5 `common_overrides`) and the output folder. Nothing is saved implicitly: a first
 * Preset/Policy is created only through "check → save" here.
 */
const { core, v1, library, form } = useV1();
const { t, reason } = useCodeText();

const presetItems = computed(() => library.value.presets.items.map((p: PresetV1) => ({ title: t("v1.lib.itemTitle", { name: p.name, revision: p.revision }), value: p.id })));
const policyItems = computed(() => library.value.policies.items.map((p: PolicyV1) => ({ title: t("v1.lib.itemTitle", { name: p.name, revision: p.revision }), value: p.id })));
const preset = computed(() => library.value.preset.item);
const policy = computed(() => library.value.policy.item);
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

// ---- explicit creation (check → save)
const presetForm = ref<PresetDraft & { open: boolean; confirm: boolean }>({ open: false, confirm: false, name: "", model: DEFAULT_PRESET_MODEL, language: "ja", formats: ["srt", "json", "md"] });
const presetIssues = computed(() => presetDraftIssues(presetForm.value));
const presetPreview = computed(() => summarize(presetSettings(presetForm.value)));
function togglePresetFormat(f: OutputFormatV1, on: boolean) {
  const next = on ? [...new Set([...presetForm.value.formats, f])] : presetForm.value.formats.filter((x) => x !== f);
  presetForm.value.formats = OUTPUT_FORMATS_V1.filter((x) => next.includes(x));
}
async function savePreset() {
  const { name, model, language, formats } = presetForm.value;
  if (await v1.library.createPreset({ name, model, language, formats })) presetForm.value = { ...presetForm.value, open: false, confirm: false, name: "" };
}
const policyForm = ref({ open: false, confirm: false, name: "" });
async function savePolicy() {
  if (await v1.library.createPolicy(policyForm.value.name)) policyForm.value = { open: false, confirm: false, name: "" };
}
const draft = computed(() => form.value.draft);
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-settings-heading" data-testid="v1-settings" :data-lists="`${library.presets.phase}/${library.policies.phase}`">
    <h2 id="v1-settings-heading" class="u-subheading">{{ t("v1.lib.title") }}</h2>
    <p class="text-caption">{{ t("v1.lib.explain") }}</p>

    <!-- Preset -->
    <h3 class="u-subheading">{{ t("v1.lib.preset") }}</h3>
    <ErrorAlert v-if="library.presets.phase === 'error' && library.presets.error" :error="library.presets.error" testid="v1-presets-error" @retry="v1.library.loadList('preset')" />
    <v-alert v-if="library.presets.truncated" type="warning" density="compact" data-testid="v1-presets-truncated">{{ t("v1.lib.truncated") }}</v-alert>
    <p v-if="library.presets.phase === 'done' && library.presets.items.length === 0" class="text-body-2" data-testid="v1-presets-empty">{{ t("v1.lib.presetEmpty") }}</p>
    <div class="u-row">
      <v-select
        :label="t('v1.lib.preset')"
        :items="presetItems"
        :model-value="preset?.id ?? null"
        :loading="library.presets.phase === 'pending' || library.preset.phase === 'pending'"
        :disabled="presetItems.length === 0"
        hide-details
        data-testid="v1-preset-select"
        @update:model-value="(v: string | null) => v1.library.select('preset', v)"
      />
      <v-btn variant="text" size="small" :loading="library.presets.phase === 'pending'" data-testid="v1-lib-reload" @click="v1.library.load()">{{ t("v1.lib.reload") }}</v-btn>
    </div>
    <ErrorAlert v-if="library.preset.phase === 'error' && library.preset.error" :error="library.preset.error" :title="t('v1.lib.selectFailed')" testid="v1-preset-select-error" />
    <div v-if="preset && presetSummary" class="u-stack" data-testid="v1-preset-frozen">
      <p class="text-body-2">{{ t("v1.lib.frozen", { name: preset.name, revision: preset.revision }) }}</p>
      <p class="text-caption u-break" data-testid="v1-preset-summary">
        {{ t("v1.lib.summary", { backend: presetSummary.backend, model: presetSummary.model, language: langText(presetSummary.language), formats: presetSummary.formats.join(", ") }) }}
      </p>
      <p class="text-caption">{{ presetSummary.diarization ? t("v1.lib.diarizationOn") : t("v1.lib.diarizationOff") }}</p>
      <v-alert v-if="library.preset.newerRevision !== null" type="info" density="compact" data-testid="v1-preset-newer">
        {{ t("v1.lib.newer", { revision: library.preset.newerRevision }) }}
        <template #append>
          <v-btn variant="text" size="small" data-testid="v1-preset-adopt" @click="v1.library.adoptNewer('preset')">{{ t("v1.lib.adopt") }}</v-btn>
        </template>
      </v-alert>
      <v-alert v-if="library.preset.missing" type="error" density="compact" data-testid="v1-preset-missing">{{ t("v1.lib.missing") }}</v-alert>
    </div>
    <div class="u-row">
      <v-btn variant="outlined" size="small" data-testid="v1-preset-create-open" @click="presetForm.open = !presetForm.open">{{ t("v1.create.presetOpen") }}</v-btn>
    </div>
    <v-card v-if="presetForm.open" variant="outlined" class="pa-3 u-stack" data-testid="v1-preset-create">
      <p class="text-caption">{{ t("v1.create.presetExplain") }}</p>
      <v-text-field v-model="presetForm.name" :label="t('v1.create.name')" maxlength="120" :disabled="presetForm.confirm" autocomplete="off" hide-details="auto" data-testid="v1-preset-name" />
      <v-text-field v-model="presetForm.model" :label="t('v1.create.model')" maxlength="200" :disabled="presetForm.confirm" autocomplete="off" spellcheck="false" :hint="t('v1.create.modelHint')" persistent-hint data-testid="v1-preset-model" />
      <v-select v-model="presetForm.language" :label="t('tx.options.language')" :items="V1_LANGUAGES.map((l) => ({ title: langText(l), value: l }))" :disabled="presetForm.confirm" hide-details data-testid="v1-preset-language" />
      <fieldset class="u-fieldset">
        <legend class="text-body-2">{{ t("tx.options.formats") }}</legend>
        <div class="u-row">
          <v-checkbox
            v-for="f in OUTPUT_FORMATS_V1"
            :key="f"
            :label="f"
            :model-value="presetForm.formats.includes(f)"
            :disabled="presetForm.confirm"
            density="compact"
            hide-details
            :data-testid="`v1-preset-format-${f}`"
            @update:model-value="(on: boolean | null) => togglePresetFormat(f, on === true)"
          />
        </div>
      </fieldset>
      <ul v-if="presetIssues.length > 0" class="text-caption u-notes" data-testid="v1-preset-issues">
        <li v-for="i in presetIssues" :key="i">{{ t(`v1.create.issue.${i}` as MessageKey) }}</li>
      </ul>
      <template v-if="presetForm.confirm">
        <v-alert type="info" density="compact" data-testid="v1-preset-confirm">
          <p>{{ t("v1.create.confirmTitle") }}</p>
          <p class="u-break">{{ t("v1.lib.summary", { backend: presetPreview.backend, model: presetPreview.model, language: langText(presetPreview.language), formats: presetPreview.formats.join(", ") }) }}</p>
          <ul class="u-notes text-caption">
            <li>{{ t("v1.create.fixedDiarization") }}</li>
            <li>{{ t("v1.create.fixedDefaults") }}</li>
            <li>{{ t("v1.create.notReady") }}</li>
          </ul>
        </v-alert>
        <div class="u-row">
          <v-btn color="primary" :loading="library.createPreset.phase === 'pending'" :disabled="presetIssues.length > 0 || library.createPreset.phase === 'unknown'" data-testid="v1-preset-save" @click="savePreset">{{ t("v1.create.save") }}</v-btn>
          <v-btn variant="text" :disabled="library.createPreset.phase === 'pending'" data-testid="v1-preset-back" @click="presetForm.confirm = false">{{ t("v1.create.back") }}</v-btn>
        </div>
      </template>
      <div v-else class="u-row">
        <v-btn variant="outlined" :disabled="presetIssues.length > 0" data-testid="v1-preset-check" @click="presetForm.confirm = true">{{ t("v1.create.check") }}</v-btn>
      </div>
      <ErrorAlert v-if="library.createPreset.phase === 'rejected' && library.createPreset.error" :error="library.createPreset.error" :title="t('v1.create.rejected')" testid="v1-preset-create-rejected" />
      <ul v-if="library.createPreset.error?.issues.length" class="text-caption u-notes" data-testid="v1-preset-create-issues">
        <li v-for="i in library.createPreset.error.issues" :key="i.path + i.code">{{ reason(i.code) }} ({{ i.path }})</li>
      </ul>
      <v-alert v-if="library.createPreset.phase === 'unknown'" type="warning" density="compact" data-testid="v1-preset-create-unknown">
        {{ t("v1.create.unknown") }}
        <template #append>
          <v-btn variant="text" size="small" data-testid="v1-preset-create-recheck" @click="v1.library.recheckCreate('preset')">{{ t("v1.unknown.recheck") }}</v-btn>
          <v-btn variant="text" size="small" data-testid="v1-preset-create-dismiss" @click="v1.library.dismissCreate('preset')">{{ t("v1.unknown.acknowledge") }}</v-btn>
        </template>
      </v-alert>
    </v-card>

    <!-- Policy -->
    <h3 class="u-subheading">{{ t("v1.lib.policy") }}</h3>
    <ErrorAlert v-if="library.policies.phase === 'error' && library.policies.error" :error="library.policies.error" testid="v1-policies-error" @retry="v1.library.loadList('policy')" />
    <v-alert v-if="library.policies.truncated" type="warning" density="compact" data-testid="v1-policies-truncated">{{ t("v1.lib.truncated") }}</v-alert>
    <p v-if="library.policies.phase === 'done' && library.policies.items.length === 0" class="text-body-2" data-testid="v1-policies-empty">{{ t("v1.lib.policyEmpty") }}</p>
    <v-select
      :label="t('v1.lib.policy')"
      :items="policyItems"
      :model-value="policy?.id ?? null"
      :loading="library.policies.phase === 'pending' || library.policy.phase === 'pending'"
      :disabled="policyItems.length === 0"
      hide-details
      data-testid="v1-policy-select"
      @update:model-value="(v: string | null) => v1.library.select('policy', v)"
    />
    <ErrorAlert v-if="library.policy.phase === 'error' && library.policy.error" :error="library.policy.error" :title="t('v1.lib.selectFailed')" testid="v1-policy-select-error" />
    <div v-if="policy" data-testid="v1-policy-frozen">
      <p class="text-body-2">{{ t("v1.lib.frozen", { name: policy.name, revision: policy.revision }) }}</p>
      <p class="text-caption">{{ t("v1.lib.policySummary", { mode: policy.mode, profiles: policy.allowedProfileIds.join(", ") || t("v1.lib.anyProfile"), fallback: policy.fallback }) }}</p>
      <v-alert v-if="library.policy.newerRevision !== null" type="info" density="compact" data-testid="v1-policy-newer">
        {{ t("v1.lib.newer", { revision: library.policy.newerRevision }) }}
        <template #append>
          <v-btn variant="text" size="small" @click="v1.library.adoptNewer('policy')">{{ t("v1.lib.adopt") }}</v-btn>
        </template>
      </v-alert>
      <v-alert v-if="library.policy.missing" type="error" density="compact" data-testid="v1-policy-missing">{{ t("v1.lib.missing") }}</v-alert>
    </div>
    <div class="u-row">
      <v-btn variant="outlined" size="small" data-testid="v1-policy-create-open" @click="policyForm.open = !policyForm.open">{{ t("v1.create.policyOpen") }}</v-btn>
    </div>
    <v-card v-if="policyForm.open" variant="outlined" class="pa-3 u-stack" data-testid="v1-policy-create">
      <v-text-field v-model="policyForm.name" :label="t('v1.create.name')" maxlength="120" :disabled="policyForm.confirm" autocomplete="off" hide-details="auto" data-testid="v1-policy-name" />
      <template v-if="policyForm.confirm">
        <v-alert type="info" density="compact" data-testid="v1-policy-confirm">{{ t("v1.create.policyContent") }}</v-alert>
        <div class="u-row">
          <v-btn color="primary" :loading="library.createPolicy.phase === 'pending'" :disabled="policyForm.name.trim() === '' || library.createPolicy.phase === 'unknown'" data-testid="v1-policy-save" @click="savePolicy">{{ t("v1.create.save") }}</v-btn>
          <v-btn variant="text" :disabled="library.createPolicy.phase === 'pending'" @click="policyForm.confirm = false">{{ t("v1.create.back") }}</v-btn>
        </div>
      </template>
      <div v-else class="u-row">
        <v-btn variant="outlined" :disabled="policyForm.name.trim() === ''" data-testid="v1-policy-check" @click="policyForm.confirm = true">{{ t("v1.create.check") }}</v-btn>
      </div>
      <ErrorAlert v-if="library.createPolicy.phase === 'rejected' && library.createPolicy.error" :error="library.createPolicy.error" :title="t('v1.create.rejected')" testid="v1-policy-create-rejected" />
      <v-alert v-if="library.createPolicy.phase === 'unknown'" type="warning" density="compact" data-testid="v1-policy-create-unknown">
        {{ t("v1.create.unknown") }}
        <template #append>
          <v-btn variant="text" size="small" data-testid="v1-policy-create-recheck" @click="v1.library.recheckCreate('policy')">{{ t("v1.unknown.recheck") }}</v-btn>
          <v-btn variant="text" size="small" @click="v1.library.dismissCreate('policy')">{{ t("v1.unknown.acknowledge") }}</v-btn>
        </template>
      </v-alert>
    </v-card>

    <!-- Differences from the Preset (common_overrides) -->
    <h3 class="u-subheading">{{ t("v1.diff.title") }}</h3>
    <p class="text-caption" data-testid="v1-diff-count">{{ t("v1.diff.explain", { count: diffCount }) }}</p>
    <v-select
      :label="t('tx.options.language')"
      :items="languageItems"
      :model-value="screen.language"
      :disabled="!preset"
      hide-details
      data-testid="v1-diff-language"
      @update:model-value="(v: string | null) => v1.library.setLanguage(v)"
    />
    <fieldset class="u-fieldset" data-testid="v1-diff-formats">
      <legend class="text-body-2">{{ t("tx.options.formats") }}</legend>
      <div class="u-row">
        <v-checkbox
          v-for="f in OUTPUT_FORMATS_V1"
          :key="f"
          :label="f"
          :model-value="effectiveFormats.includes(f)"
          :disabled="!preset"
          density="compact"
          hide-details
          :data-testid="`v1-diff-format-${f}`"
          @update:model-value="(on: boolean | null) => toggleFormat(f, on === true)"
        />
      </div>
      <v-btn v-if="screen.formats !== null" variant="text" size="small" data-testid="v1-diff-formats-reset" @click="v1.library.setFormats(null)">{{ t("v1.diff.reset") }}</v-btn>
    </fieldset>

    <!-- Output folder (the S1 saved default or an explicit choice) -->
    <h3 class="u-subheading">{{ t("tx.output.title") }}</h3>
    <div class="u-row">
      <v-btn variant="outlined" :loading="form.picking === 'output_folder'" :disabled="form.picking !== null" data-testid="v1-output-pick" @click="core.transcription.pick('output_folder', core.host)">{{ t("tx.output.pick") }}</v-btn>
    </div>
    <v-text-field
      :label="t('tx.output.label')"
      :model-value="draft.outputDir"
      :readonly="form.picking === 'output_folder'"
      :maxlength="MAX_JOB_PATH_LENGTH + 1"
      autocomplete="off"
      spellcheck="false"
      hide-details="auto"
      data-testid="v1-output-path"
      @update:model-value="(v: string) => core.transcription.edit({ outputDir: v ?? '' })"
    />
    <p v-if="draft.outputSource" class="text-caption">{{ t(`tx.output.source.${draft.outputSource}` as MessageKey) }}</p>
    <v-alert v-if="form.pickError" type="info" role="status">{{ form.pickError.reason === "host_unavailable" ? t("tx.pick.hostUnavailable") : t("tx.pick.failed") }}</v-alert>
    <p class="text-caption">{{ t("v1.output.checked") }}</p>
  </section>
</template>
