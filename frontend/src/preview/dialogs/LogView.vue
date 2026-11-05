<script setup lang="ts">
import { mdiExportVariant } from "@mdi/js";
import { computed, ref, watch } from "vue";
import { usePreview } from "../composables";
import { jobLog, LOG_EXPORT_FORMATS, type LogExportFormat, logFileName, type LogLine, setupLog, SYSTEM_LOG } from "../domain/diagnostics";
import { isPreviewKey, type PreviewKey } from "../i18n";
import SaveMock from "./SaveMock.vue";

/**
 * System / job / setup log viewer with "Export…" (iteration 4, owner memo #17). The job log
 * exports the ONE job chosen above it. Export goes to the shared "save as" step; nothing is written.
 */
const props = defineProps<{ kind: "systemLog" | "jobLog" | "setupLog" }>();
const { setupState, state, t } = usePreview();
const jobId = ref<string | null>(state.value.selectedJobId ?? state.value.jobs[0]?.id ?? null);
const exporting = ref(false);
const format = ref<LogExportFormat>("log");
watch(() => props.kind, () => (exporting.value = false));
const jobs = computed(() => state.value.jobs.map((j) => ({ value: j.id, title: `${j.id} — ${j.inputName}` })));
const job = computed(() => state.value.jobs.find((j) => j.id === jobId.value) ?? null);
const lines = computed<LogLine[]>(() => {
  if (props.kind === "systemLog") return [...SYSTEM_LOG];
  if (props.kind === "setupLog") return setupLog(setupState.value);
  return job.value ? jobLog(job.value) : [];
});
const text = (line: LogLine) => {
  const params = { ...line.params };
  if (typeof params.language === "string" && isPreviewKey(`lang.${params.language}`)) params.language = t(`lang.${params.language}` as PreviewKey);
  if (typeof params.stage === "string" && isPreviewKey(`stage.${params.stage}`)) params.stage = t(`stage.${params.stage}` as PreviewKey);
  const msg = isPreviewKey(line.key) ? t(line.key, params) : line.key;
  return `${line.time}  ${line.level.padEnd(5)}  ${line.source.padEnd(12)}  ${msg}`;
};
const exportKind = computed(() => (props.kind === "systemLog" ? "system" : props.kind === "jobLog" ? "job" : "setup"));
const target = computed(() => (props.kind === "jobLog" ? t("logExport.targetJob", { id: job.value?.id ?? "—" }) : t(`logExport.target.${exportKind.value}` as PreviewKey)));
const formats = computed(() => LOG_EXPORT_FORMATS.map((f) => ({ value: f, title: t(`logExport.format.${f}` as PreviewKey) })));
</script>

<template>
  <div class="u-stack" :data-testid="`log-view-${kind}`">
    <template v-if="!exporting">
      <p class="text-body-medium">{{ t(`dialog.${kind}.lead` as PreviewKey) }}</p>
      <v-select v-if="kind === 'jobLog'" v-model="jobId" :items="jobs" :label="t('dialog.jobLog.select')" :no-data-text="t('dialog.jobLog.none')" data-testid="joblog-select" />
      <p v-if="kind === 'jobLog' && jobs.length === 0" class="text-body-medium" data-testid="joblog-empty">{{ t("dialog.jobLog.none") }}</p>
      <pre v-if="lines.length > 0" class="p-log" data-testid="log-lines">{{ lines.map(text).join("\n") }}</pre>
      <p class="text-body-small">{{ t("dialog.log.masking") }}</p>
      <div class="u-row">
        <v-spacer />
        <v-btn variant="tonal" color="primary" :prepend-icon="mdiExportVariant" :disabled="lines.length === 0" data-testid="log-export" @click="exporting = true">{{ t("logExport.button") }}</v-btn>
      </div>
    </template>
    <template v-else>
      <h3 class="u-subheading">{{ t("logExport.title") }}</h3>
      <v-select v-model="format" :items="formats" :label="t('save.format')" hide-details data-testid="log-export-format" />
      <SaveMock :target="target" :file-name="logFileName(exportKind, format, job?.id)" :format-label="t('logExport.encoding')" testid="log" @back="exporting = false">
        <p class="text-body-small">{{ t("dialog.log.masking") }}</p>
      </SaveMock>
    </template>
  </div>
</template>
