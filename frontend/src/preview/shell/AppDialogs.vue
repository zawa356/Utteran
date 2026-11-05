<script setup lang="ts">
import { mdiCheck, mdiClose, mdiDownloadOutline, mdiMinusCircleOutline, mdiOpenInNew } from "@mdi/js";
import { computed, ref, watch } from "vue";
import { usePreview } from "../composables";
import { ABOUT } from "../domain/about";
import { DIAGNOSTICS_EXCLUDED, DIAGNOSTICS_INCLUDED, jobLog, type LogLine, SYSTEM_LOG } from "../domain/diagnostics";
import { isPreviewKey, type PreviewKey } from "../i18n";

/**
 * Dialogs opened from the app menu (Tools / Help). Mock only: the logs are fixed samples, the
 * diagnostics export creates no file, nothing is read from disk or the backend. Each dialog says
 * so once in its own header chip; the product pages stay free of such notes.
 */
const { layout, layoutState, state, t } = usePreview();
const open = computed({
  get: () => layoutState.value.dialog !== null,
  set: (v: boolean) => !v && layout.openDialog(null),
});
const dialog = computed(() => layoutState.value.dialog);
const titleKey = computed<PreviewKey>(() => {
  switch (dialog.value) {
    case "systemLog":
      return "dialog.systemLog.title";
    case "jobLog":
      return "dialog.jobLog.title";
    case "diagnostics":
      return "dialog.diagnostics.title";
    default:
      return "dialog.about.title";
  }
});

const jobId = ref<string | null>(null);
watch(dialog, (d) => {
  if (d === "jobLog") jobId.value = state.value.selectedJobId ?? state.value.jobs[0]?.id ?? null;
});
const jobs = computed(() => state.value.jobs.map((j) => ({ value: j.id, title: `${j.id} — ${j.inputName}` })));
const job = computed(() => state.value.jobs.find((j) => j.id === jobId.value) ?? null);
const lines = computed<LogLine[]>(() => (dialog.value === "systemLog" ? [...SYSTEM_LOG] : job.value ? jobLog(job.value) : []));
const text = (line: LogLine) => {
  const params = { ...line.params };
  if (typeof params.language === "string" && isPreviewKey(`lang.${params.language}`)) params.language = t(`lang.${params.language}` as PreviewKey);
  const msg = isPreviewKey(line.key) ? t(line.key, params) : line.key;
  return `${line.time}  ${line.level.padEnd(5)}  ${line.source.padEnd(12)}  ${msg}`;
};
const close = () => layout.openDialog(null);
</script>

<template>
  <v-dialog v-model="open" max-width="720" scrollable data-testid="app-dialog">
    <v-card v-if="dialog" variant="elevated" :data-testid="`dialog-${dialog}`">
      <v-card-title class="d-flex align-center ga-2 flex-wrap">
        <span class="p-card__title">{{ t(titleKey) }}</span>
        <v-chip v-if="dialog !== 'about'" label color="warning" variant="outlined" data-testid="dialog-demo-chip">{{ t("dialog.demoChip") }}</v-chip>
        <v-spacer />
        <v-btn variant="text" :icon="mdiClose" size="small" :aria-label="t('common.close')" data-testid="dialog-close" @click="close" />
      </v-card-title>
      <v-card-text class="u-stack">
        <!-- logs -->
        <template v-if="dialog === 'systemLog' || dialog === 'jobLog'">
          <p class="text-body-medium">{{ t(dialog === "systemLog" ? "dialog.systemLog.lead" : "dialog.jobLog.lead") }}</p>
          <v-select
            v-if="dialog === 'jobLog'"
            v-model="jobId"
            :items="jobs"
            :label="t('dialog.jobLog.select')"
            :no-data-text="t('dialog.jobLog.none')"
            data-testid="joblog-select"
          />
          <p v-if="dialog === 'jobLog' && jobs.length === 0" class="text-body-medium" data-testid="joblog-empty">{{ t("dialog.jobLog.none") }}</p>
          <pre v-if="lines.length > 0" class="p-log" data-testid="log-lines">{{ lines.map(text).join("\n") }}</pre>
          <p class="text-body-small">{{ t("dialog.log.masking") }}</p>
        </template>

        <!-- diagnostics -->
        <template v-else-if="dialog === 'diagnostics'">
          <p class="text-body-medium">{{ t("dialog.diagnostics.lead") }}</p>
          <div>
            <div class="font-weight-medium mb-1">{{ t("dialog.diagnostics.included") }}</div>
            <ul class="p-bullets" data-testid="diag-included">
              <li v-for="k in DIAGNOSTICS_INCLUDED" :key="k"><v-icon :icon="mdiCheck" size="small" color="success" aria-hidden="true" /> {{ t(k) }}</li>
            </ul>
          </div>
          <div>
            <div class="font-weight-medium mb-1">{{ t("dialog.diagnostics.excluded") }}</div>
            <ul class="p-bullets" data-testid="diag-excluded">
              <li v-for="k in DIAGNOSTICS_EXCLUDED" :key="k"><v-icon :icon="mdiMinusCircleOutline" size="small" aria-hidden="true" /> {{ t(k) }}</li>
            </ul>
          </div>
        </template>

        <!-- about -->
        <template v-else>
          <div class="u-row">
            <span class="text-h6 font-weight-bold">{{ ABOUT.name }}</span>
            <v-chip label color="warning" variant="outlined" data-testid="about-dev-chip">{{ t("dialog.about.devChip") }}</v-chip>
          </div>
          <dl class="p-dl text-body-medium" data-testid="about-facts">
            <dt>{{ t("dialog.about.version") }}</dt>
            <dd data-testid="about-version">{{ t("dialog.about.versionValue", { version: ABOUT.version }) }}</dd>
            <dt>{{ t("dialog.about.license") }}</dt>
            <dd>{{ ABOUT.license }}</dd>
            <dt>{{ t("dialog.about.links") }}</dt>
            <dd>
              <ul class="p-bullets">
                <li><a :href="ABOUT.repository" target="_blank" rel="noopener noreferrer" data-testid="about-repo">{{ t("dialog.about.repo") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
                <li><a :href="ABOUT.licenseUrl" target="_blank" rel="noopener noreferrer">{{ t("dialog.about.licenseText") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
                <li><a :href="ABOUT.noticesUrl" target="_blank" rel="noopener noreferrer">{{ t("dialog.about.notices") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
              </ul>
            </dd>
          </dl>
          <p class="text-body-small">{{ t("dialog.about.note") }}</p>
        </template>
      </v-card-text>
      <v-card-actions>
        <template v-if="dialog === 'diagnostics'">
          <span class="text-body-small px-2" data-testid="diag-disabled-reason">{{ t("dialog.diagnostics.disabled") }}</span>
          <v-spacer />
          <v-btn variant="tonal" color="primary" :prepend-icon="mdiDownloadOutline" disabled data-testid="diag-export">{{ t("dialog.diagnostics.export") }}</v-btn>
        </template>
        <v-spacer v-else />
        <v-btn variant="text" data-testid="dialog-ok" @click="close">{{ t("common.close") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
