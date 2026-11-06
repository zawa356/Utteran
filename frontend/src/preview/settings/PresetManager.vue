<script setup lang="ts">
import { mdiContentCopy, mdiDeleteOutline, mdiExportVariant, mdiImport, mdiPencilOutline, mdiPlus } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { allPresets } from "../domain/demo";
import { hasPrivateText, presetKeys } from "../domain/presets";
import PresetExportDialog from "./PresetExportDialog.vue";
import PresetImportDialog from "./PresetImportDialog.vue";
import { usePresetName } from "./usePresetName";
import { useSettingText } from "./useSettingText";

/**
 * Presets (Settings → Presets, Expert; iteration 4, owner memo #20): select one, then new / edit /
 * duplicate / delete / import / export. Built-in presets are read-only — "Duplicate" is the way to
 * a changed copy. In memory only (no file, no B5 Preset CRUD yet).
 */
const { demo, state, t } = usePreview();
const { presetName } = usePresetName();
const { valueText, fieldName } = useSettingText();
const presets = computed(() => allPresets(state.value));
const selectedId = ref<string>(presets.value[0]!.id);
const selected = computed(() => presets.value.find((p) => p.id === selectedId.value) ?? presets.value[0]!);
const dialog = ref<"new" | "edit" | "delete" | "import" | "export" | null>(null);
const name = ref("");
const replaceContent = ref(false);

function openNew() {
  name.value = t("preset.newName");
  dialog.value = "new";
}
function openEdit() {
  name.value = selected.value.name;
  replaceContent.value = false;
  dialog.value = "edit";
}
function create() {
  selectedId.value = demo.createPresetFromCommon(name.value.trim() || t("preset.newName"));
  dialog.value = null;
}
function saveEdit() {
  demo.renamePreset(selected.value.id, name.value);
  if (replaceContent.value) demo.replacePresetWithCommon(selected.value.id);
  dialog.value = null;
}
function duplicate() {
  const id = demo.duplicatePreset(selected.value.id, t("preset.copyName", { name: presetName(selected.value) }));
  if (id) selectedId.value = id;
}
function remove() {
  demo.deletePreset(selected.value.id);
  selectedId.value = presets.value[0]!.id;
  dialog.value = null;
}
function imported(id: string) {
  selectedId.value = id;
  dialog.value = null;
}
const summary = (p: (typeof presets.value)[number]) => t("preset.items", { n: presetKeys(p).length });
</script>

<template>
  <div class="u-stack" data-testid="preset-manager">
    <p class="text-body-medium">{{ t("settings.presets.lead") }}</p>
    <div class="u-row" role="toolbar" :aria-label="t('preset.toolbar')">
      <v-btn variant="tonal" color="primary" :prepend-icon="mdiPlus" data-testid="preset-new" @click="openNew">{{ t("preset.new") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiPencilOutline" :disabled="selected.builtIn" data-testid="preset-edit" @click="openEdit">{{ t("preset.edit") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiContentCopy" data-testid="preset-duplicate" @click="duplicate">{{ t("preset.duplicate") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiDeleteOutline" :disabled="selected.builtIn" data-testid="preset-delete" @click="dialog = 'delete'">{{ t("preset.delete") }}</v-btn>
      <v-spacer />
      <v-btn variant="text" :prepend-icon="mdiImport" data-testid="preset-import" @click="dialog = 'import'">{{ t("preset.import") }}</v-btn>
      <v-btn variant="text" :prepend-icon="mdiExportVariant" data-testid="preset-export" @click="dialog = 'export'">{{ t("preset.export") }}</v-btn>
    </div>
    <div class="p-presets">
      <v-card>
        <v-list color="primary" lines="two" :aria-label="t('settings.cat.presets')" data-testid="settings-presets">
          <v-list-item
            v-for="p in presets"
            :key="p.id"
            :title="presetName(p)"
            :subtitle="summary(p)"
            :active="p.id === selectedId"
            :aria-current="p.id === selectedId ? 'true' : undefined"
            :data-testid="`preset-row-${p.id}`"
            @click="selectedId = p.id"
          >
            <template #append>
              <v-chip v-if="p.builtIn" label variant="outlined">{{ t("preset.builtIn") }}</v-chip>
              <v-chip v-else-if="hasPrivateText(p)" label variant="outlined">{{ t("preset.hasText") }}</v-chip>
            </template>
          </v-list-item>
        </v-list>
      </v-card>
      <v-card class="pa-3" data-testid="preset-detail">
        <h3 class="u-subheading">{{ presetName(selected) }}</h3>
        <dl class="p-dl text-body-medium mt-2">
          <template v-for="k in presetKeys(selected)" :key="k">
            <dt>{{ fieldName(k) }}</dt>
            <dd>{{ valueText(k, selected.values[k]) }}</dd>
          </template>
        </dl>
        <p v-if="selected.builtIn" class="text-body-small mt-2">{{ t("preset.builtInHint") }}</p>
      </v-card>
    </div>

    <v-dialog :model-value="dialog === 'new' || dialog === 'edit'" max-width="480" @update:model-value="(v) => !v && (dialog = null)">
      <v-card variant="elevated" :title="dialog === 'new' ? t('preset.new') : t('preset.edit')" data-testid="preset-name-dialog">
        <v-card-text class="u-stack">
          <v-text-field v-model="name" :label="t('preset.name')" counter="120" maxlength="120" data-testid="preset-name" />
          <p v-if="dialog === 'new'" class="text-body-medium">{{ t("preset.newFromCommon") }}</p>
          <v-checkbox v-else v-model="replaceContent" :label="t('preset.replaceContent')" hide-details color="primary" data-testid="preset-replace" />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = null">{{ t("common.cancel") }}</v-btn>
          <v-btn color="primary" variant="tonal" :disabled="name.trim() === ''" data-testid="preset-name-save" @click="dialog === 'new' ? create() : saveEdit()">{{ t("preset.save") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog :model-value="dialog === 'delete'" max-width="440" @update:model-value="(v) => !v && (dialog = null)">
      <v-card variant="elevated" :title="t('preset.deleteTitle', { name: presetName(selected) })" data-testid="preset-delete-dialog">
        <v-card-text>{{ t("preset.deleteBody") }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="dialog = null">{{ t("common.cancel") }}</v-btn>
          <v-btn color="error" variant="tonal" data-testid="preset-delete-confirm" @click="remove">{{ t("preset.delete") }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <PresetImportDialog v-if="dialog === 'import'" @close="dialog = null" @imported="imported" />
    <PresetExportDialog v-if="dialog === 'export'" :preset="selected" @close="dialog = null" />
  </div>
</template>
