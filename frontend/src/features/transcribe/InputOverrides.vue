<script setup lang="ts">
import { computed } from "vue";
import type { JobLanguage, OutputFormat } from "../../api/legacyEnvironment";
import { useI18n } from "../../composables/core";
import { useTranscription } from "../../composables/transcription";
import { type FieldSummary, formatState as stateOf, type OverrideField, summarize } from "../../domain/inputCollection";
import { MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import type { MessageKey } from "../../i18n";

/**
 * Expert ②: per-file differences from the common settings, for the selection. A mixed selection
 * is shown as mixed (never the first row's value); nothing changes until "Apply"; "Use common"
 * removes the override. Choices come only from the current environment answer. Rows that were
 * sent (accepted/unknown/sending) keep their frozen values and are not edited.
 */
const { core, list, form, common, gate } = useTranscription();
const { t } = useI18n();

// `list`/`gate` are read so the selection follows row and ledger changes.
const rows = computed(() => (list.value && gate.value ? core.inputs.editableSelection() : []));
const selected = computed(() => list.value.rows.filter((r) => r.selected).length);
const locked = computed(() => selected.value - rows.value.length);
const staged = computed(() => list.value.staged);
const options = computed(() => common.value.options);
const sum = (field: OverrideField | "output"): FieldSummary => summarize(rows.value, form.value.draft, field);

const backendSummary = computed(() => sum("asrBackend"));
const backendValue = computed(() => staged.value.asrBackend ?? (backendSummary.value.mixed ? null : (backendSummary.value.value as string | null)));
const backend = computed(() => options.value?.asr.find((b) => b.id === backendValue.value) ?? null);
const backendItems = computed(() => (options.value?.asr ?? []).map((b) => ({ title: b.label, value: b.id })));
const modelItems = computed(() => (backend.value?.models ?? []).map((m) => ({ title: m.label, value: m.id })));
const deviceItems = computed(() => (backend.value?.devices ?? []).map((d) => ({ title: d.label, value: d.id })));
const languageItems = computed(() => (options.value?.languages ?? []).map((l) => ({ title: t(`tx.lang.${l}` as MessageKey), value: l })));

function shown(field: "asrBackend" | "asrModel" | "asrDevice" | "language"): string | null {
  const s = staged.value[field];
  if (s !== undefined) return s;
  if (field !== "asrBackend" && staged.value.asrBackend !== undefined && field !== "language") return null;
  const summary = sum(field);
  return summary.mixed ? null : (summary.value as string | null);
}
function hint(field: OverrideField | "output"): string {
  const s = sum(field);
  const parts: string[] = [];
  if (s.mixed) parts.push(t("tx.ov.mixed", { count: s.count }));
  parts.push(s.overridden > 0 ? t("tx.ov.overridden", { count: s.overridden }) : t("tx.ov.inherited"));
  const pending = field === "formats" ? staged.value.formatChanges !== undefined : field !== "output" && staged.value[field] !== undefined;
  if (pending) parts.push(t("tx.ov.staged"));
  return parts.join(" · ");
}

// Formats: checked = every row has it, indeterminate = only some rows have it. A click stages a
// change of that one format only; every other format stays as each row has it (PR #43 P2-1).
function formatState(f: OutputFormat): { checked: boolean; indeterminate: boolean } {
  return stateOf(rows.value, form.value.draft, staged.value, f);
}
function toggleFormat(f: OutputFormat, on: boolean) {
  core.inputs.stageFormat(f, on);
}

const outputSummary = computed(() => sum("output"));
const outputMode = computed<"inherit" | "custom" | null>(() => {
  if (staged.value.output) return staged.value.output.kind;
  const custom = rows.value.filter((r) => r.output.kind === "custom").length;
  return custom === 0 ? "inherit" : custom === rows.value.length ? "custom" : null;
});
const outputPath = computed(() => {
  if (staged.value.output?.kind === "custom") return staged.value.output.path;
  return outputMode.value === "custom" && !outputSummary.value.mixed ? ((outputSummary.value.value as string | null) ?? "") : "";
});
const problems = computed(() => (list.value ? core.inputs.stagedProblems() : { output: null, formats: false }));
const hasStaged = computed(() => Object.keys(staged.value).length > 0);
const canApply = computed(() => rows.value.length > 0 && hasStaged.value && problems.value.output === null && !problems.value.formats);
const commonOutput = computed(() => form.value.draft.outputDir);

function setOutputMode(mode: "inherit" | "custom" | null) {
  if (mode === "inherit") core.inputs.stage({ output: { kind: "inherit" } });
  else if (mode === "custom") core.inputs.stage({ output: { kind: "custom", path: outputPath.value } });
}
</script>

<template>
  <section class="u-stack" aria-labelledby="tx-ov-heading" data-testid="tx-ov">
    <h2 id="tx-ov-heading" class="u-subheading">{{ t("tx.ov.title") }}</h2>
    <p class="text-body-2" data-testid="tx-ov-count">{{ t("tx.ov.count", { count: rows.length }) }}</p>
    <p v-if="locked > 0" class="text-caption" data-testid="tx-ov-locked">{{ t("tx.ov.locked", { count: locked }) }}</p>
    <v-alert v-if="rows.length === 0" type="info" density="compact" data-testid="tx-ov-empty">{{ t("tx.ov.empty") }}</v-alert>
    <template v-else>
      <v-alert v-if="!options" type="info" density="compact" data-testid="tx-ov-no-options">{{ t("tx.ov.noOptions") }}</v-alert>
      <v-select
        :label="t('tx.options.backend')"
        :items="backendItems"
        :model-value="shown('asrBackend')"
        :placeholder="backendSummary.mixed ? t('tx.ov.mixedShort') : undefined"
        persistent-placeholder
        :hint="hint('asrBackend')"
        persistent-hint
        :disabled="!options"
        data-testid="tx-ov-backend"
        @update:model-value="(v: string) => core.inputs.stage({ asrBackend: v })"
      />
      <v-select
        :label="t('tx.options.model')"
        :items="modelItems"
        :model-value="shown('asrModel')"
        :placeholder="sum('asrModel').mixed ? t('tx.ov.mixedShort') : undefined"
        persistent-placeholder
        :hint="backend ? hint('asrModel') : t('tx.ov.backendFirst')"
        persistent-hint
        :disabled="!backend || modelItems.length === 0"
        data-testid="tx-ov-model"
        @update:model-value="(v: string) => core.inputs.stage({ asrModel: v })"
      />
      <v-select
        :label="t('tx.options.device')"
        :items="deviceItems"
        :model-value="shown('asrDevice')"
        :placeholder="sum('asrDevice').mixed ? t('tx.ov.mixedShort') : undefined"
        persistent-placeholder
        :hint="backend ? hint('asrDevice') : t('tx.ov.backendFirst')"
        persistent-hint
        :disabled="!backend || deviceItems.length === 0"
        data-testid="tx-ov-device"
        @update:model-value="(v: string) => core.inputs.stage({ asrDevice: v })"
      />
      <v-select
        :label="t('tx.options.language')"
        :items="languageItems"
        :model-value="shown('language')"
        :placeholder="sum('language').mixed ? t('tx.ov.mixedShort') : undefined"
        persistent-placeholder
        :hint="hint('language')"
        persistent-hint
        :disabled="!options"
        data-testid="tx-ov-language"
        @update:model-value="(v: string) => core.inputs.stage({ language: v as JobLanguage })"
      />
      <fieldset class="u-fieldset" data-testid="tx-ov-formats">
        <legend class="text-body-2">{{ t("tx.options.formats") }}</legend>
        <div class="u-row">
          <v-checkbox
            v-for="f in options?.formats ?? []"
            :key="f"
            :label="f"
            :model-value="formatState(f).checked"
            :indeterminate="formatState(f).indeterminate"
            density="compact"
            hide-details
            :data-testid="`tx-ov-format-${f}`"
            @update:model-value="(on: boolean | null) => toggleFormat(f, on === true)"
          />
        </div>
        <p class="text-caption">{{ hint("formats") }}</p>
        <p v-if="problems.formats" class="text-caption text-error" data-testid="tx-ov-formats-empty">{{ t("tx.ov.formatsEmpty") }}</p>
      </fieldset>

      <fieldset class="u-fieldset" data-testid="tx-ov-output">
        <legend class="text-body-2">{{ t("tx.output.title") }}</legend>
        <v-radio-group :model-value="outputMode" inline hide-details @update:model-value="(v: unknown) => setOutputMode(v as 'inherit' | 'custom' | null)">
          <v-radio :label="t('tx.ov.outputInherit')" value="inherit" data-testid="tx-ov-output-inherit" />
          <v-radio :label="t('tx.ov.outputCustom')" value="custom" data-testid="tx-ov-output-custom" />
        </v-radio-group>
        <p v-if="outputMode === null" class="text-caption" data-testid="tx-ov-output-mixed">{{ t("tx.ov.mixed", { count: rows.length }) }}</p>
        <p class="text-caption u-break" data-testid="tx-ov-output-common">{{ commonOutput ? t("tx.ov.commonOutput", { folder: commonOutput }) : t("tx.output.noDefault") }}</p>
        <template v-if="outputMode === 'custom'">
          <div class="u-row u-row--field">
            <v-text-field
              :label="t('tx.output.label')"
              :model-value="outputPath"
              :maxlength="MAX_JOB_PATH_LENGTH + 1"
              :error-messages="problems.output ? [t(`tx.path.${problems.output}` as MessageKey)] : []"
              autocomplete="off"
              spellcheck="false"
              data-testid="tx-ov-output-path"
              @update:model-value="(v: string) => core.inputs.stage({ output: { kind: 'custom', path: v ?? '' } })"
            />
            <v-btn variant="outlined" :loading="list.picking" :disabled="list.picking" data-testid="tx-ov-output-pick" @click="core.inputs.pickOutput(core.host)">{{ t("tx.output.pick") }}</v-btn>
          </div>
        </template>
      </fieldset>

      <div class="u-row">
        <v-btn color="primary" variant="tonal" :disabled="!canApply" data-testid="tx-ov-apply" @click="core.inputs.applyStaged()">{{ t("tx.ov.apply", { count: rows.length }) }}</v-btn>
        <v-btn variant="text" :disabled="!hasStaged" data-testid="tx-ov-discard" @click="core.inputs.discardStaged()">{{ t("tx.ov.discard") }}</v-btn>
        <v-btn variant="text" data-testid="tx-ov-reset" @click="core.inputs.resetToCommon(['asrBackend', 'asrModel', 'asrDevice', 'language', 'formats', 'output'])">{{ t("tx.ov.reset", { count: rows.length }) }}</v-btn>
      </div>
      <p class="text-caption">{{ t("tx.ov.note") }}</p>
    </template>
  </section>
</template>
