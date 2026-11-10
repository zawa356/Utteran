<script setup lang="ts">
import { computed } from "vue";
import type { JobLanguage, OutputFormat } from "../../api/legacyEnvironment";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import type { MessageKey } from "../../i18n";

/**
 * Common processing settings (S3-A form, S3-B1: shared by every input that does not override a
 * value). Choices come only from the *current* environment answer (`jobOptions`), never from a
 * static table. Labels are display text; the ids are sent.
 */
const { core, form, common: evaluation, commonIssue: issue } = useTranscription();
const { t } = useI18n();
const draft = computed(() => form.value.draft);
const options = computed(() => evaluation.value.options);
const envBlock = computed(() => issue("environment"));
const backendItems = computed(() => (options.value?.asr ?? []).map((b) => ({ title: b.label, value: b.id })));
const modelItems = computed(() => (evaluation.value.backend?.models ?? []).map((m) => ({ title: m.label, value: m.id })));
const deviceItems = computed(() => (evaluation.value.backend?.devices ?? []).map((d) => ({ title: d.label, value: d.id })));
const languageItems = computed(() => (options.value?.languages ?? []).map((l) => ({ title: t(`tx.lang.${l}` as MessageKey), value: l })));
const formats = computed<readonly OutputFormat[]>(() => options.value?.formats ?? []);
const disabled = computed(() => options.value === null);

function outputProblem(): string[] {
  const i = issue("output");
  if (!i || draft.value.outputDir === "") return [];
  return [t(`tx.path.${i.problem}` as MessageKey)];
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-options-heading" data-testid="tx-options">
    <h2 id="tx-options-heading" class="u-subheading">{{ t("tx.options.title") }}</h2>
    <p class="text-caption" data-testid="tx-options-common">{{ t("tx.options.common") }}</p>
    <v-alert v-if="envBlock" type="info" role="status" data-testid="tx-env-block">{{ t(`tx.env.${envBlock.problem}` as MessageKey) }}</v-alert>
    <template v-else>
      <p class="text-body-2" data-testid="tx-profile">{{ t("tx.options.profile", { profile: evaluation.profile ?? "" }) }}</p>
      <p class="text-caption" data-testid="tx-unverified">{{ t("tx.options.unverified") }}</p>
      <v-alert v-if="options && options.asr.length === 0" type="warning" data-testid="tx-no-backend">{{ t("tx.options.noBackend") }}</v-alert>
      <v-alert v-if="options?.incomplete" type="info" density="compact" data-testid="tx-incomplete">{{ t("tx.options.incomplete") }}</v-alert>
    </template>
    <v-select
      :label="t('tx.options.backend')"
      :items="backendItems"
      :model-value="draft.asrBackend"
      :disabled="disabled || backendItems.length === 0"
      data-testid="tx-backend"
      @update:model-value="(v: string | null) => core.transcription.choose('asrBackend', v)"
    />
    <v-select
      :label="t('tx.options.model')"
      :items="modelItems"
      :model-value="draft.asrModel"
      :disabled="disabled || modelItems.length === 0"
      data-testid="tx-model"
      @update:model-value="(v: string | null) => core.transcription.choose('asrModel', v)"
    />
    <v-select
      :label="t('tx.options.device')"
      :items="deviceItems"
      :model-value="draft.asrDevice"
      :disabled="disabled || deviceItems.length === 0"
      data-testid="tx-device"
      @update:model-value="(v: string | null) => core.transcription.choose('asrDevice', v)"
    />
    <v-select
      :label="t('tx.options.language')"
      :items="languageItems"
      :model-value="draft.language"
      :disabled="disabled || languageItems.length === 0"
      data-testid="tx-language"
      @update:model-value="(v: JobLanguage) => core.transcription.edit({ language: v })"
    />
    <fieldset class="u-fieldset" data-testid="tx-formats">
      <legend class="text-body-2">{{ t("tx.options.formats") }}</legend>
      <div class="u-row">
        <v-checkbox
          v-for="f in formats"
          :key="f"
          :label="f"
          :model-value="draft.formats.includes(f)"
          density="compact"
          hide-details
          :data-testid="`tx-format-${f}`"
          @update:model-value="(on: boolean | null) => core.transcription.toggleFormat(f, on === true)"
        />
      </div>
    </fieldset>
    <p class="text-caption" data-testid="tx-diarization-off">{{ t("tx.options.diarizationOff") }}</p>
    <p class="text-caption">{{ t("tx.options.resume") }}</p>

    <h3 class="u-subheading">{{ t("tx.output.title") }}</h3>
    <div class="u-row">
      <v-btn
        variant="outlined"
        :loading="form.picking === 'output_folder'"
        :disabled="form.picking !== null"
        data-testid="tx-output-pick"
        @click="core.transcription.pick('output_folder', core.host)"
      >
        {{ t("tx.output.pick") }}
      </v-btn>
    </div>
    <v-text-field
      :label="t('tx.output.label')"
      :model-value="draft.outputDir"
      :readonly="form.picking === 'output_folder'"
      :maxlength="MAX_JOB_PATH_LENGTH + 1"
      :error-messages="outputProblem()"
      autocomplete="off"
      spellcheck="false"
      hide-details="auto"
      data-testid="tx-output-path"
      @update:model-value="(v: string) => core.transcription.edit({ outputDir: v ?? '' })"
    />
    <p v-if="draft.outputSource" class="text-caption" data-testid="tx-output-source">{{ t(`tx.output.source.${draft.outputSource}` as MessageKey) }}</p>
    <p v-else-if="draft.outputDir === ''" class="text-caption" data-testid="tx-output-no-default">{{ t("tx.output.noDefault") }}</p>
    <p v-if="form.picking === 'output_folder'" role="status" class="text-body-2">{{ t("tx.output.picking") }}</p>
    <v-alert v-if="form.pickError" type="info" role="status" data-testid="tx-output-pick-error">
      {{ form.pickError.reason === "host_unavailable" ? t("tx.pick.hostUnavailable") : t("tx.pick.failed") }}
    </v-alert>
    <ul class="text-caption u-notes">
      <li>{{ t("tx.output.notChecked") }}</li>
      <li>{{ t("tx.output.naming") }}</li>
      <li data-testid="tx-output-remember">{{ t("tx.output.remember") }}</li>
    </ul>
  </section>
</template>
