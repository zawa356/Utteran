<script setup lang="ts">
import { computed } from "vue";
import { useV1 } from "../../composables/v1";
import { useI18n } from "../../composables/core";
import { MAX_JOB_PATH_LENGTH } from "../../domain/paths";
import type { MessageKey } from "../../i18n";

/**
 * The output folder of the screen (the S1 saved default or an explicit choice), split out of the
 * former V1Settings so the Design Freeze can show it with the file ("保存先"). Behaviour unchanged.
 */
const { core, form } = useV1();
const { t } = useI18n();
const draft = computed(() => form.value.draft);
</script>

<template>
  <section class="u-stack u-tight" aria-labelledby="v1-output-heading" data-testid="v1-output">
    <!-- Output folder (the S1 saved default or an explicit choice); Design Freeze "保存先". -->
    <h3 id="v1-output-heading" class="u-field__label">{{ t("tx.output.title") }}</h3>
    <div class="u-row u-row--field">
      <v-text-field
        :label="t('tx.output.label')"
        :model-value="draft.outputDir"
        :readonly="form.picking === 'output_folder'"
        :maxlength="MAX_JOB_PATH_LENGTH + 1"
        :hint="draft.outputSource ? t(`tx.output.source.${draft.outputSource}` as MessageKey) : t('v1.output.checked')"
        persistent-hint
        density="compact"
        autocomplete="off"
        spellcheck="false"
        data-testid="v1-output-path"
        @update:model-value="(v: string) => core.transcription.edit({ outputDir: v ?? '' })"
      />
      <v-btn variant="outlined" class="u-field-btn" :loading="form.picking === 'output_folder'" :disabled="form.picking !== null" data-testid="v1-output-pick" @click="core.transcription.pick('output_folder', core.host)">{{ t("tx.output.pick") }}</v-btn>
    </div>
    <p v-if="draft.outputSource" class="text-caption">{{ t("v1.output.checked") }}</p>
    <v-alert v-if="form.pickError" type="info" role="status">{{ form.pickError.reason === "host_unavailable" ? t("tx.pick.hostUnavailable") : t("tx.pick.failed") }}</v-alert>
  </section>
</template>
