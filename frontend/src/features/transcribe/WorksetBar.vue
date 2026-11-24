<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { checkName, MAX_NAME } from "../../domain/v1/worksets";
import type { MessageKey } from "../../i18n";

/**
 * E-B (`--next-ui-v1`): the named workset of this screen, shared by Normal and Expert (one
 * controller). Saving is automatic; the status shows only what the server confirmed. Switching,
 * creating and restoring first confirm the save; deleting asks first and removes only the
 * workset (never inputs, outputs, jobs or history).
 */
const core = useCore();
const worksets = core.v1!.worksets;
const s = useStore(worksets.store);
const inputs = useStore(core.inputs.store);
const { t, language } = useI18n();

const items = computed(() => s.value.items.map((i) => ({ title: i.name, value: i.id, subtitle: t("eb.rowsCount", { count: i.inputCount }) })));
const busy = computed(() => s.value.op.phase === "pending");
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleString(language.value === "ja" ? "ja-JP" : "en-US") : "");
const status = computed(() => {
  if (s.value.phase === "loading" || s.value.phase === "idle") return { key: "eb.status.loading" as MessageKey, color: "info", phase: "loading" };
  if (s.value.phase === "error") return { key: "eb.status.offline" as MessageKey, color: "error", phase: "offline" };
  const phase = s.value.save.phase;
  const colors: Record<string, string> = { clean: "success", dirty: "info", saving: "info", error: "error", unknown: "warning", conflict: "error" };
  return { key: `eb.status.${phase}` as MessageKey, color: colors[phase] ?? "info", phase };
});
const statusText = computed(() => (status.value.phase === "clean" && s.value.save.savedAt ? t("eb.status.cleanAt", { time: time(s.value.save.savedAt) }) : t(status.value.key)));
const blockText = computed(() => {
  const op = s.value.op;
  if (op.phase !== "error" || !op.block) return null;
  return t(`eb.block.${op.block}` as MessageKey, { max: s.value.limits?.maxWorksets ?? 20 });
});
const missing = computed(() => inputs.value.rows.filter((r) => r.availability === "missing" || r.availability === "not_file").length);

// ---- switching -----------------------------------------------------------------------------
async function choose(id: string | null) {
  if (!id || id === s.value.current?.id) return;
  if (core.inputs.hasTransientEdits() && !window.confirm(t("eb.switchTransient"))) return;
  await worksets.switchTo(id);
}

// ---- rename --------------------------------------------------------------------------------
const renameOpen = ref(false);
const renameValue = ref("");
const renameField = ref<{ focus: () => void } | null>(null);
const renameValid = computed(() => checkName(renameValue.value) !== null);
// IME: Enter while a composition is open only confirms the conversion (some hosts report
// `isComposing` late, so the composition events are tracked as well).
const composing = ref(false);
async function openRename() {
  renameValue.value = s.value.current?.name ?? "";
  renameOpen.value = true;
  await nextTick();
  renameField.value?.focus();
}
function submitRename(event?: KeyboardEvent) {
  if (event && (composing.value || event.isComposing || event.keyCode === 229)) return;
  if (!renameValid.value) return;
  if (worksets.rename(renameValue.value)) renameOpen.value = false;
}

// ---- delete --------------------------------------------------------------------------------
const deleteTarget = ref<{ id: string; name: string; current: boolean } | null>(null);
function askDelete() {
  const current = s.value.current;
  if (current) deleteTarget.value = { id: current.id, name: current.name, current: true };
}
async function confirmDelete() {
  const target = deleteTarget.value;
  if (!target) return;
  if (await worksets.remove(target.id)) deleteTarget.value = null;
}

// ---- recovery ------------------------------------------------------------------------------
const recoveryOpen = ref(false);
async function openRecovery() {
  recoveryOpen.value = true;
  await worksets.loadRecovery();
}
async function restore(revision: number) {
  if (await worksets.restoreRecovery(revision)) recoveryOpen.value = false;
}

// ---- page lifetime -------------------------------------------------------------------------
function beforeUnload(event: BeforeUnloadEvent) {
  if (!worksets.hasUnsaved()) return;
  void worksets.flush();
  event.preventDefault();
}
function onVisibility() {
  if (document.visibilityState === "hidden") void worksets.flush();
  else void worksets.checkRemote();
}
function onFocus() {
  void worksets.checkRemote();
}
onMounted(() => {
  window.addEventListener("beforeunload", beforeUnload);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("focus", onFocus);
});
onUnmounted(() => {
  window.removeEventListener("beforeunload", beforeUnload);
  document.removeEventListener("visibilitychange", onVisibility);
  window.removeEventListener("focus", onFocus);
});
</script>

<template>
  <section class="u-stack u-workset" aria-labelledby="ws-heading" data-testid="ws-bar" :data-phase="status.phase" :data-current="s.current?.id ?? ''">
    <div class="u-row u-workset-row">
      <h2 id="ws-heading" class="u-subheading u-no-shrink">{{ t("eb.label") }}</h2>
      <v-select
        class="u-workset-select"
        :label="t('eb.select')"
        :items="items"
        :model-value="s.current?.id ?? null"
        :disabled="s.phase !== 'ready' || busy"
        :loading="s.phase === 'loading' || (busy && s.op.kind === 'switch')"
        item-props
        density="compact"
        hide-details
        data-testid="ws-select"
        @update:model-value="choose"
      />
      <v-chip :color="status.color" size="small" variant="tonal" role="status" aria-live="polite" data-testid="ws-status" :data-phase="status.phase">{{ statusText }}</v-chip>
      <v-menu>
        <template #activator="{ props: menu }">
          <v-btn v-bind="menu" variant="text" size="small" :disabled="s.phase !== 'ready' || busy" :aria-label="t('eb.menu')" data-testid="ws-menu">{{ t("eb.menu") }}</v-btn>
        </template>
        <v-list density="compact">
          <v-list-item :title="t('eb.new')" data-testid="ws-new" @click="worksets.create(false)" />
          <v-list-item :title="t('eb.duplicate')" data-testid="ws-duplicate" @click="worksets.create(true)" />
          <v-list-item :title="t('eb.rename')" data-testid="ws-rename" @click="openRename" />
          <v-list-item :title="t('eb.recovery')" data-testid="ws-recovery" @click="openRecovery" />
          <v-list-item :title="t('eb.delete')" :disabled="s.items.length <= 1" data-testid="ws-delete" @click="askDelete" />
        </v-list>
      </v-menu>
    </div>
    <p class="text-caption">{{ t("eb.explain") }}</p>

    <v-alert v-if="s.phase === 'error' && s.error" type="error" density="compact" data-testid="ws-load-error">
      {{ t("eb.status.offline") }}
      <ErrorAlert :error="s.error" testid="ws-load-error-detail" />
      <template #append>
        <v-btn variant="text" size="small" data-testid="ws-load-retry" @click="worksets.start()">{{ t("common.retry") }}</v-btn>
      </template>
    </v-alert>
    <ErrorAlert v-if="s.save.phase === 'error' && s.save.error" :error="s.save.error" :title="t('eb.status.error')" testid="ws-save-error" :on-retry="() => worksets.retry()" />
    <v-alert v-if="s.save.phase === 'unknown'" type="warning" density="compact" data-testid="ws-save-unknown">{{ t("eb.unknown") }}</v-alert>

    <v-alert v-if="s.conflict" type="error" density="compact" data-testid="ws-conflict" :data-kind="s.conflict.kind">
      {{ t(`eb.conflict.${s.conflict.kind}` as MessageKey, { revision: s.conflict.remote?.revision ?? "" }) }}
      <div class="u-row mt-2">
        <v-btn v-if="s.conflict.remote" variant="outlined" size="small" data-testid="ws-conflict-load" @click="worksets.loadRemote()">{{ t("eb.conflict.loadRemote") }}</v-btn>
        <v-btn v-if="s.conflict.remote" variant="outlined" size="small" data-testid="ws-conflict-keep" @click="worksets.overwriteRemote()">{{ t("eb.conflict.keepLocal") }}</v-btn>
        <v-btn v-if="s.conflict.kind === 'deleted'" variant="outlined" size="small" data-testid="ws-conflict-save-new" @click="worksets.saveAsNew()">{{ t("eb.conflict.saveAsNew") }}</v-btn>
      </div>
    </v-alert>

    <v-alert v-if="blockText" type="warning" density="compact" closable data-testid="ws-block" @click:close="worksets.dismissOp()">{{ blockText }}</v-alert>
    <ErrorAlert v-if="s.op.phase === 'error' && s.op.error" :error="s.op.error" testid="ws-op-error" />
    <v-alert v-if="s.op.phase === 'unknown'" type="warning" density="compact" data-testid="ws-create-unknown">
      {{ t("eb.createUnknown") }}
      <template #append>
        <v-btn variant="text" size="small" data-testid="ws-create-recheck" @click="worksets.recheckCreate()">{{ t("eb.recheckCreate") }}</v-btn>
        <v-btn variant="text" size="small" @click="worksets.dismissOp()">{{ t("common.close") }}</v-btn>
      </template>
    </v-alert>

    <v-alert v-if="missing > 0 || s.check.unchecked > 0" :type="missing > 0 ? 'warning' : 'info'" density="compact" data-testid="ws-missing" :data-missing="missing">
      <span v-if="missing > 0">{{ t("eb.missing", { count: missing }) }}</span>
      <span v-if="s.check.unchecked > 0"> {{ t("eb.unchecked", { count: s.check.unchecked }) }}</span>
      <template #append>
        <v-btn variant="text" size="small" :loading="s.check.phase === 'pending'" data-testid="ws-recheck" @click="worksets.checkInputs()">{{ t("eb.recheck") }}</v-btn>
      </template>
    </v-alert>

    <v-dialog v-model="renameOpen" max-width="480" :aria-label="t('eb.rename')" data-testid="ws-rename-dialog">
      <v-card :title="t('eb.rename')">
        <v-card-text>
          <v-text-field
            ref="renameField"
            v-model="renameValue"
            :label="t('eb.renameLabel')"
            :maxlength="MAX_NAME"
            :error-messages="renameValid ? [] : [t('eb.block.name_invalid')]"
            autocomplete="off"
            data-testid="ws-rename-input"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @keydown.enter="submitRename"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" data-testid="ws-rename-cancel" @click="renameOpen = false">{{ t("common.cancel") }}</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!renameValid" data-testid="ws-rename-save" @click="submitRename()">{{ t("common.save") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="deleteTarget !== null" max-width="560" :persistent="busy" :aria-label="t('eb.delete')" data-testid="ws-delete-dialog" @update:model-value="(v) => { if (!v && !busy) deleteTarget = null; }">
      <v-card v-if="deleteTarget" :title="t('eb.deleteTitle', { name: deleteTarget.name })">
        <v-card-text class="u-stack">
          <p>{{ t("eb.deleteBody") }}</p>
          <p v-if="deleteTarget.current" class="text-body-2">{{ t("eb.deleteCurrent") }}</p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="busy" data-testid="ws-delete-cancel" @click="deleteTarget = null">{{ t("common.cancel") }}</v-btn>
          <v-btn color="error" variant="flat" :loading="busy" data-testid="ws-delete-confirm" @click="confirmDelete">{{ t("eb.deleteConfirm") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="recoveryOpen" max-width="560" scrollable :aria-label="t('eb.recovery')" data-testid="ws-recovery-dialog">
      <v-card :title="t('eb.recovery')">
        <v-card-text class="u-stack">
          <p class="text-body-2">{{ t("eb.recoveryNote", { n: s.limits?.recoveryGenerations ?? 2 }) }}</p>
          <ErrorAlert v-if="s.recovery.phase === 'error' && s.recovery.error" :error="s.recovery.error" testid="ws-recovery-error" />
          <p v-if="s.recovery.phase === 'done' && s.recovery.items.length === 0" data-testid="ws-recovery-empty">{{ t("eb.recoveryEmpty") }}</p>
          <v-list v-if="s.recovery.items.length > 0" density="compact">
            <v-list-item v-for="item in s.recovery.items" :key="item.revision" data-testid="ws-recovery-item" :title="item.name" :subtitle="t('eb.recoveryItem', { revision: item.revision, time: time(item.savedAt), count: item.inputCount })">
              <template #append>
                <v-btn variant="outlined" size="small" :disabled="busy" data-testid="ws-recovery-restore" @click="restore(item.revision)">{{ t("eb.recoveryRestore") }}</v-btn>
              </template>
            </v-list-item>
          </v-list>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="recoveryOpen = false">{{ t("common.close") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<style scoped>
.u-workset-row {
  align-items: center;
  flex-wrap: wrap;
}
.u-workset-select {
  min-width: 12rem;
  max-width: 24rem;
  flex: 1 1 14rem;
}
</style>
