<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { OUTPUT_FORMATS_V1, type OutputFormatV1 } from "../../api/v1Resources";
import { useCore } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { useCodeText } from "../../composables/v1";
import type { MessageKey } from "../../i18n";
import { ja } from "../../i18n/ja";

/**
 * E-C: re-export from the verified JSON (no ASR). Standard Vuetify parts only. Cancel has the
 * initial focus; closing returns focus to the opener. While a request is in flight or its outcome
 * is unknown the dialog is persistent and the form is frozen.
 */
const core = useCore();
const reexport = core.v1!.reexport;
const state = useStore(reexport.store);
const { t } = useCodeText();
const cancelButton = ref<{ $el: HTMLElement } | null>(null);
const picking = ref(false);
const pickerError = ref(false);
let returnFocus: HTMLElement | null = null;

const open = computed(() => state.value.step !== "closed");
const locked = computed(() => {
  void state.value;
  return reexport.locked();
});
const form = computed(() => state.value.form);
const source = computed(() => state.value.source);
const issues = computed(() => {
  void state.value;
  return reexport.issues();
});
const editable = computed(() => (state.value.step === "form" || state.value.step === "rejected") && !!source.value?.available);
const canSubmit = computed(() => {
  void state.value;
  return reexport.canSubmit();
});
/** Fixed code -> text; an unknown code stays visible after a generic sentence. */
const code = (value: string | null | undefined) => {
  if (!value) return "";
  const key = `ec.code.${value}`;
  return key in ja ? t(key as MessageKey) : `${t("ec.failed")} (${value})`;
};

function toggle(f: OutputFormatV1, on: boolean): void {
  const current = form.value?.formats ?? [];
  reexport.edit({ formats: on ? [...current, f] : current.filter(x => x !== f) });
}

function onOpenChange(value: boolean): void {
  if (!value) reexport.close();
}

// Keep keyboard focus inside the modal when the primary button is disabled or replaced
// (PR review P3-9): the in-progress text, the recheck button, the refusal or the close button.
const focusFor: Record<string, string> = {
  submitting: "reexport-submitting",
  unknown: "reexport-recheck",
  rejected: "reexport-rejected",
  done: "reexport-close",
};
watch(() => state.value.step, async (step) => {
  const target = focusFor[step];
  if (!target) return;
  await nextTick();
  document.querySelector<HTMLElement>(`[data-testid="reexport-dialog"] [data-testid="${target}"]`)?.focus({ preventScroll: true });
});

watch(open, (value) => {
  if (!value) return;
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  pickerError.value = false;
}, { flush: "sync" });

async function pickParent(): Promise<void> {
  if (picking.value || !editable.value) return;
  picking.value = true;
  pickerError.value = false;
  try {
    const path = await core.host.pickPath("output_folder");
    if (path) reexport.edit({ parent: path });
  } catch {
    pickerError.value = true;
  } finally {
    picking.value = false;
  }
}

function restoreFocus(): void {
  if (returnFocus?.isConnected) returnFocus.focus();
  else document.querySelector<HTMLElement>('[data-testid="v1-board-refresh"], [data-testid="nav-results"]')?.focus();
  returnFocus = null;
}
</script>

<template>
  <v-dialog
    :model-value="open" max-width="640" :persistent="locked || picking" scrollable
    :aria-label="t('ec.title')" @update:model-value="onOpenChange" @after-enter="cancelButton?.$el.focus({ preventScroll: true })" @after-leave="restoreFocus"
  >
    <v-card class="u-v1-dialog" variant="flat" color="surface" elevation="8" :title="t('ec.title')" data-testid="reexport-dialog" :data-step="state.step">
      <v-card-text class="u-stack u-break">
        <p>{{ t("ec.explain") }}</p>
        <p class="text-caption">{{ t("ec.notRerun") }}</p>
        <p v-if="state.jobId" class="text-caption">{{ t("ec.source", { id: state.jobId }) }}</p>
        <p v-if="state.step === 'loading'" role="status">{{ t("ec.loading") }}</p>
        <v-alert v-else-if="state.step === 'load_error'" type="warning" density="compact" role="status" data-testid="reexport-load-error">
          {{ state.error?.code ? code(state.error.code) : t("ec.loadFailed") }}
        </v-alert>
        <v-alert v-else-if="source && !source.available" type="info" density="compact" role="status" data-testid="reexport-refusal" :data-refusal="source.refusal">
          <strong>{{ t("ec.unavailable") }}</strong> {{ code(source.refusal) }}
        </v-alert>
        <template v-if="source?.available && form && state.step !== 'done'">
          <p class="text-body-2" data-testid="reexport-source-summary">
            {{ t("ec.summary", { segments: source.source!.segments, words: source.source!.words, speakers: source.source!.speakers.length }) }}
          </p>
          <fieldset class="u-fieldset" data-testid="reexport-formats">
            <legend class="text-body-2">{{ t("tx.options.formats") }}</legend>
            <div class="u-row">
              <v-checkbox
                v-for="f in OUTPUT_FORMATS_V1" :key="f" :label="f" :model-value="form.formats.includes(f)" :disabled="!editable"
                density="compact" hide-details :data-testid="`reexport-format-${f}`"
                @update:model-value="(on: boolean | null) => toggle(f, on === true)"
              />
            </div>
            <p v-if="issues.includes('formats')" class="text-caption text-error" data-testid="reexport-formats-empty">{{ t("ec.formatsEmpty") }}</p>
          </fieldset>
          <v-switch
            :model-value="form.showSpeaker" :label="t('ec.showSpeaker')" :disabled="!editable" color="primary" density="compact"
            :hint="t('ec.showSpeakerJson')" persistent-hint data-testid="reexport-show-speaker"
            @update:model-value="(v: boolean | null) => reexport.edit({ showSpeaker: v === true })"
          />
          <v-switch
            :model-value="form.srtBom" :label="t('ec.srtBom')" :disabled="!editable || !form.formats.includes('srt')" color="primary" density="compact"
            :hint="form.formats.includes('srt') ? undefined : t('ec.srtBomNotApplicable')" :persistent-hint="!form.formats.includes('srt')" data-testid="reexport-srt-bom"
            @update:model-value="(v: boolean | null) => reexport.edit({ srtBom: v === true })"
          />
          <v-radio-group
            :model-value="form.newline" :label="t('ec.newline')" inline :disabled="!editable" density="compact" hide-details data-testid="reexport-newline"
            @update:model-value="(v: unknown) => reexport.edit({ newline: v === 'crlf' ? 'crlf' : 'lf' })"
          >
            <v-radio label="LF" value="lf" data-testid="reexport-newline-lf" />
            <v-radio label="CRLF" value="crlf" data-testid="reexport-newline-crlf" />
          </v-radio-group>
          <fieldset class="u-fieldset" data-testid="reexport-labels">
            <legend class="text-body-2">{{ t("ec.labels") }}</legend>
            <p class="text-caption">{{ t(source.originalLabelsApplied ? "ec.labelsApplied" : "ec.labelsDisplayed") }}</p>
            <p v-if="source.source!.speakers.length === 0" class="text-caption" data-testid="reexport-no-speakers">{{ t("ec.noSpeakers") }}</p>
            <v-text-field
              v-for="(label, index) in source.source!.speakers" :key="index"
              :model-value="form.labels[label] ?? label" :label="label === '' ? t('ec.emptyLabel') : label" :disabled="!editable"
              :maxlength="source.limits.maxLabel" density="compact" hide-details="auto" autocomplete="off" spellcheck="false"
              :data-testid="`reexport-label-${index}`"
              @update:model-value="(v: string | null) => reexport.setLabel(label, v ?? '')"
            />
            <p class="text-caption">{{ t("ec.labelEmptyKeeps") }}</p>
            <p v-if="issues.includes('label')" class="text-caption text-error">{{ t("ec.labelInvalid") }}</p>
          </fieldset>
          <fieldset class="u-fieldset" data-testid="reexport-parent">
            <legend class="text-body-2">{{ t("ec.parent") }}</legend>
            <v-radio-group
              :model-value="form.parent === null ? 'default' : 'explicit'" :disabled="!editable" density="compact" hide-details
              @update:model-value="(v: unknown) => reexport.edit({ parent: v === 'explicit' ? (form?.parent ?? '') : null })"
            >
              <v-radio :label="t('ec.parentDefault', { path: source.defaultParent })" value="default" data-testid="reexport-parent-default" />
              <v-radio :label="t('ec.parentExplicit')" value="explicit" data-testid="reexport-parent-explicit" />
            </v-radio-group>
            <div v-if="form.parent !== null" class="u-row">
              <v-text-field
                :model-value="form.parent" :label="t('ec.parentPath')" :disabled="!editable" density="compact" hide-details="auto"
                autocomplete="off" spellcheck="false" data-testid="reexport-parent-path"
                @update:model-value="(v: string | null) => reexport.edit({ parent: v ?? '' })"
              />
              <v-btn variant="outlined" size="small" :loading="picking" :disabled="!editable" data-testid="reexport-parent-pick" @click="pickParent">{{ t("ec.pick") }}</v-btn>
            </div>
            <p v-if="pickerError" role="status">{{ t("tx.pick.failed") }}</p>
            <p v-if="issues.includes('parent')" class="text-caption text-error">{{ t("ec.parentInvalid") }}</p>
            <p class="text-caption">{{ t("ec.newDirectory") }}</p>
          </fieldset>
          <p v-if="state.step === 'submitting'" role="status" tabindex="-1" data-testid="reexport-submitting">{{ t("ec.submitting") }}</p>
          <v-alert v-if="state.step === 'rejected'" type="warning" density="compact" role="status" tabindex="-1" data-testid="reexport-rejected" :data-code="state.error?.code ?? ''">
            {{ state.error?.code ? code(state.error.code) : t("ec.failed") }}
          </v-alert>
          <v-alert v-if="state.step === 'unknown'" type="warning" density="compact" role="status" data-testid="reexport-unknown">{{ t("ec.unknown") }}</v-alert>
        </template>
        <template v-if="state.step === 'done' && state.record">
          <v-alert
            :type="state.record.state === 'completed' ? 'success' : state.record.state === 'pending' ? 'info' : 'warning'" density="compact" role="status"
            data-testid="reexport-result" :data-state="state.record.state" :data-code="state.record.errorCode ?? ''"
          >
            {{ t(`ec.state.${state.record.state}` as MessageKey) }}
            <span v-if="state.record.errorCode"> {{ code(state.record.errorCode) }}</span>
          </v-alert>
          <p v-if="state.record.directory" class="text-body-2" data-testid="reexport-directory">{{ t("ec.directory", { path: state.record.directory }) }}</p>
          <ul v-if="state.record.files.length" class="u-notes text-body-2" data-testid="reexport-files">
            <li v-for="file in state.record.files" :key="file.format" :data-format="file.format">{{ t("ec.file", { format: file.format.toUpperCase(), size: file.size }) }}</li>
          </ul>
          <p v-if="state.record.partial" class="text-caption" data-testid="reexport-partial">{{ t("ec.partial") }}</p>
          <p v-if="state.record.state === 'completed' && !state.record.locationVerified" class="text-caption">{{ t("ec.locationUnverified") }}</p>
        </template>
        <details v-if="state.history.length" data-testid="reexport-history">
          <summary class="text-body-2">{{ t("ec.history", { count: state.history.length }) }}</summary>
          <ul class="u-notes text-caption">
            <li v-for="item in state.history" :key="item.id" :data-state="item.state">
              {{ item.createdAt }} · {{ t(`ec.state.${item.state}` as MessageKey) }} · {{ item.formats.join(", ") }}
            </li>
          </ul>
        </details>
      </v-card-text>
      <v-card-actions class="u-row flex-wrap">
        <v-btn ref="cancelButton" :disabled="locked || picking" data-testid="reexport-close" @click="reexport.close()">
          {{ t(state.step === "done" ? "v1.board.action.close" : "common.cancel") }}
        </v-btn>
        <v-btn v-if="state.step === 'unknown'" color="primary" data-testid="reexport-recheck" @click="reexport.recheck()">{{ t("v1.unknown.recheck") }}</v-btn>
        <v-btn v-else-if="state.step === 'load_error'" color="primary" data-testid="reexport-reload" @click="reexport.reload()">{{ t("ec.reload") }}</v-btn>
        <template v-else-if="state.step === 'done'">
          <v-btn v-if="state.record?.state === 'pending'" variant="outlined" data-testid="reexport-refresh" @click="reexport.refreshRecord()">{{ t("ec.refresh") }}</v-btn>
          <v-btn variant="outlined" data-testid="reexport-again" @click="reexport.again()">{{ t("ec.again") }}</v-btn>
        </template>
        <v-btn v-else-if="source?.available" color="primary" :loading="state.step === 'submitting'" :disabled="!canSubmit || picking" data-testid="reexport-submit" @click="reexport.submit()">{{ t("ec.submit") }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
