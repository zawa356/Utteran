<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, nextTick, useTemplateRef } from "vue";
import type { GlossarySupport, JsonObject, PresetV1 } from "../../api/v1Resources";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCodeText, useV1 } from "../../composables/v1";
import { classifyStartFailure } from "../../domain/admission";
import { toApiError } from "../../domain/operation";
import { canonical } from "../../domain/v1/settings";
import GlossaryEditor from "./GlossaryEditor.vue";

const props = defineProps<{ preset: PresetV1 }>();
const { v1, library } = useV1();
const { t, issue } = useCodeText();
const open = ref(false);
const support = ref<ReadonlyMap<string, GlossarySupport>>(new Map());
const glossarySupport = computed(() => support.value.get(String((settings.value?.asr as JsonObject | undefined)?.backend)));
const loading = ref(false);
const base = ref(props.preset);
const latest = ref<PresetV1 | null>(null);
const name = ref("");
const settingsText = ref("");
const terms = ref<string[]>([]);
const glossaryDirty = ref(false);
const glossaryVersion = ref(0);
const action = ref<"revise" | "duplicate" | "delete" | "close" | "useLatest" | null>(null);
const error = ref<ReturnType<typeof toApiError> | null>(null);
const notice = ref<"saved" | "deleted" | "matched" | "missing" | "conflict" | "unknown" | null>(null);
const uncertain = ref<"revise" | "delete" | null>(null);
const remoteMissing = ref(false);
const needsRecheck = computed(() => uncertain.value !== null || (notice.value === "conflict" && !latest.value && !remoteMissing.value));
const blocked = computed(() => loading.value || library.value.createPreset.phase === "pending");
const editable = computed(() => !blocked.value && !action.value && !needsRecheck.value && notice.value !== "saved");

function reset(preset: PresetV1) {
  base.value = preset;
  name.value = preset.name;
  settingsText.value = JSON.stringify(preset.settings, null, 2);
  terms.value = [...((preset.settings.asr as JsonObject).glossary as string[])];
  glossaryDirty.value = false;
  glossaryVersion.value += 1;
  latest.value = null;
  notice.value = null;
  error.value = null;
}
const dirty = computed(() => open.value && notice.value !== "saved" && (glossaryDirty.value || name.value !== base.value.name ||
  settingsText.value !== JSON.stringify(base.value.settings, null, 2) ||
  canonical(terms.value) !== canonical((base.value.settings.asr as JsonObject).glossary)));

const settings = computed<JsonObject | null>(() => {
  try {
    const value = JSON.parse(settingsText.value) as JsonObject;
    if (!value?.asr || typeof value.asr !== "object" || Array.isArray(value.asr)) return null;
    return { ...value, asr: { ...(value.asr as JsonObject), glossary: [...terms.value] } };
  } catch {
    return null;
  }
});
const canSave = computed(() => settings.value !== null && name.value.trim().length > 0 && !glossaryDirty.value);

function start() {
  reset(props.preset);
  remoteMissing.value = library.value.preset.missing;
  open.value = true;
  if (support.value.size === 0) void v1.library.getGlossarySupport().then(value => { support.value = value; }, () => {});
}

function close() {
  if (blocked.value) return;
  if (action.value) {
    action.value = null;
    return;
  }
  if (dirty.value || uncertain.value) action.value = "close";
  else open.value = false;
}

async function recheck() {
  loading.value = true;
  try {
    const current = await v1.library.getLatestPreset(base.value.id);
    latest.value = current;
    remoteMissing.value = false;
    if (uncertain.value) {
      notice.value = uncertain.value === "revise" && current.revision > base.value.revision &&
        current.name === name.value.trim() && canonical(current.settings) === canonical(settings.value) ? "matched" : "unknown";
    }
  } catch (reason) {
    const failure = toApiError(reason);
    if (failure.status === 404) {
      latest.value = null;
      remoteMissing.value = true;
      notice.value = "missing";
    } else {
      error.value = failure;
    }
  } finally {
    await v1.library.loadList("preset");
    loading.value = false;
  }
}

async function confirm() {
  const choice = action.value;
  action.value = null;
  if (choice === "close") {
    uncertain.value = null;
    open.value = false;
    return;
  }
  if (choice === "useLatest") {
    useLatest();
    return;
  }
  if (!choice || blocked.value || needsRecheck.value) return;
  error.value = null;
  notice.value = null;
  const body = settings.value;
  if (choice !== "delete" && (!body || !canSave.value)) return;
  loading.value = true;
  try {
    if (choice === "duplicate") {
      if (await v1.library.duplicatePreset(name.value, body!)) open.value = false;
    } else if (choice === "delete") {
      await v1.library.deletePreset(base.value);
      notice.value = "deleted";
      remoteMissing.value = true;
    } else {
      await v1.library.revisePreset(base.value, name.value, body!);
      notice.value = "saved";
    }
  } catch (reason) {
    error.value = toApiError(reason);
    if (classifyStartFailure(error.value) === "unknown") {
      uncertain.value = choice === "delete" ? "delete" : "revise";
      notice.value = "unknown";
      await recheck();
    } else if (error.value.status === 412 || error.value.status === 404) {
      notice.value = "conflict";
      await recheck();
    }
  } finally {
    loading.value = false;
  }
}

function useLatest() {
  if (!latest.value) return;
  uncertain.value = null;
  reset(latest.value);
}

const cancel = useTemplateRef<{ $el: HTMLElement }>("cancel");
watch(action, async (pending) => {
  if (!pending) return;
  await nextTick();
  cancel.value?.$el.focus();
});

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value && !blocked.value && !uncertain.value) return;
  event.preventDefault();
  event.returnValue = "";
}
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onUnmounted(() => window.removeEventListener("beforeunload", beforeUnload));
</script>

<template>
  <v-dialog :model-value="open" persistent scrollable max-width="720" @keydown.esc.prevent.stop="(event: KeyboardEvent) => { if (!event.isComposing) close(); }">
    <template #activator="{ props: trigger }">
      <v-btn v-bind="trigger" size="small" variant="outlined" data-testid="ea-manage" @click="start">{{ t("ea.manage") }}</v-btn>
    </template>
    <v-card :title="t('ea.manage')" data-testid="ea-manager">
      <v-card-text class="u-stack">
        <p class="text-caption u-break">{{ base.id }} · r{{ base.revision }}</p>
        <v-alert v-if="notice" :type="notice === 'saved' || notice === 'deleted' ? 'info' : 'warning'" density="compact" role="status">{{ t(`ea.notice.${notice}`) }}</v-alert>
        <ErrorAlert v-if="error" :error="error" />
        <ul v-if="error?.issues.length" class="text-caption u-notes">
          <li v-for="item in error.issues" :key="item.path + item.code">{{ issue(item) }}</li>
        </ul>
        <v-alert v-if="remoteMissing" type="warning" density="compact">{{ t("ea.recover") }}</v-alert>
        <ErrorAlert v-if="library.createPreset.phase === 'rejected' && library.createPreset.error" :error="library.createPreset.error" />
        <v-alert v-if="library.createPreset.phase === 'unknown'" type="warning" density="compact">
          {{ t("v1.create.unknown") }}
          <v-btn variant="text" :disabled="blocked" @click="v1.library.recheckCreate('preset').then(ok => { if (ok) open = false; })">{{ t("v1.unknown.recheck") }}</v-btn>
          <v-btn variant="text" :disabled="blocked" @click="v1.library.dismissCreate('preset')">{{ t("v1.unknown.acknowledge") }}</v-btn>
        </v-alert>
        <v-btn v-if="needsRecheck" :disabled="blocked" :loading="loading" variant="outlined" @click="recheck">{{ t("ea.getLatest") }}</v-btn>
        <template v-if="latest">
          <p class="text-body-2">{{ t("v1.lib.frozen", { name: latest.name, revision: latest.revision }) }}</p>
          <v-textarea :model-value="JSON.stringify(latest.settings, null, 2)" :label="t('ea.latestSettings')" readonly rows="4" />
          <v-btn v-if="!action" :disabled="blocked" variant="outlined" @click="action = 'useLatest'">{{ t("ea.discardOptions") }}</v-btn>
        </template>
        <v-text-field v-model="name" :label="t('ea.projectName')" maxlength="120" autocomplete="off" :disabled="!editable" data-testid="ea-name" />
        <p v-if="glossarySupport" class="text-caption" role="status">{{ t(`ea.support.${glossarySupport}`) }}</p>
        <GlossaryEditor v-if="open" :key="glossaryVersion" :terms="terms" :disabled="!editable" @apply="terms = $event" @dirty="glossaryDirty = $event" />
        <v-expansion-panels>
          <v-expansion-panel :title="t('ea.settings')">
            <v-expansion-panel-text>
              <v-textarea v-model="settingsText" :label="t('ea.settings')" :hint="t('ea.settingsHint')" persistent-hint :disabled="!editable" autocomplete="off" spellcheck="false" rows="6" />
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        <v-alert v-if="action" type="warning" density="compact" role="status">{{ t(`ea.confirm.${action}`) }}</v-alert>
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <template v-if="action">
          <v-btn ref="cancel" :disabled="blocked" @click="action = null">{{ t("v1.create.back") }}</v-btn>
          <v-btn color="primary" :disabled="blocked" data-testid="ea-confirm" @click="confirm">{{ t(`ea.${action}`) }}</v-btn>
        </template>
        <template v-else>
          <v-btn :disabled="blocked" @click="close">{{ t("ea.close") }}</v-btn>
          <v-btn :disabled="!editable || !canSave || library.createPreset.phase === 'unknown'" data-testid="ea-duplicate" @click="action = 'duplicate'">{{ t("ea.duplicate") }}</v-btn>
          <v-btn :disabled="!editable || remoteMissing || !canSave || !dirty || notice === 'saved' || latest !== null" color="primary" data-testid="ea-revise" @click="action = 'revise'">{{ t("ea.revise") }}</v-btn>
          <v-btn :disabled="!editable || remoteMissing || notice === 'saved' || latest !== null" data-testid="ea-delete" @click="action = 'delete'">{{ t("ea.delete") }}</v-btn>
        </template>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
