<script setup lang="ts">
import { computed } from "vue";
import { MAX_FOLDER_LENGTH } from "../../api/legacy";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import type { FolderField } from "../../domain/settings";

/** One folder default: free text like the legacy GUI, plus the native chooser through HostBridge. */
const props = defineProps<{ field: FolderField; label: string; testid: string }>();
const { host, settings } = useCore();
const state = useStore(settings.store);
const { t } = useI18n();
const value = computed(() => state.value.draft?.[props.field] ?? "");
const busy = computed(() => state.value.save.phase === "pending" || state.value.reconcile.phase === "pending");
const pickingThis = computed(() => state.value.picking === props.field);
const tooLong = computed(() => value.value.length > MAX_FOLDER_LENGTH);
const pickError = computed(() => (state.value.pickError?.field === props.field ? state.value.pickError.reason : null));
</script>

<template>
  <div class="u-stack" :data-testid="testid">
    <div class="u-folder">
      <v-text-field
        :label="label"
        :model-value="value"
        :placeholder="t('settings.folderUnset')"
        persistent-placeholder
        :disabled="busy"
        :readonly="pickingThis"
        :error-messages="tooLong ? [t('settings.folderTooLong', { max: MAX_FOLDER_LENGTH })] : []"
        hide-details="auto"
        autocomplete="off"
        spellcheck="false"
        class="u-folder__input"
        :data-testid="`${testid}-input`"
        @update:model-value="(v: string) => settings.edit({ [field]: v ?? '' })"
      />
      <div class="u-row u-folder__actions">
        <v-btn
          variant="outlined"
          :loading="pickingThis"
          :disabled="busy || (state.picking !== null && !pickingThis)"
          :aria-label="t('settings.folderPickLabel', { field: label })"
          :data-testid="`${testid}-pick`"
          @click="settings.pickFolder(field, host)"
        >
          {{ t("settings.folderPick") }}
        </v-btn>
        <v-btn
          variant="text"
          :disabled="busy || state.picking !== null || value === ''"
          :aria-label="t('settings.folderClearLabel', { field: label })"
          :data-testid="`${testid}-clear`"
          @click="settings.edit({ [field]: '' })"
        >
          {{ t("settings.folderClear") }}
        </v-btn>
      </div>
    </div>
    <p v-if="pickingThis" role="status" class="text-body-2" :data-testid="`${testid}-picking`">{{ t("settings.folderPicking") }}</p>
    <v-alert v-if="pickError" type="info" role="status" :data-testid="`${testid}-pick-error`">
      {{ pickError === "host_unavailable" ? t("settings.pickHostUnavailable") : t("settings.pickFailed") }}
    </v-alert>
  </div>
</template>
