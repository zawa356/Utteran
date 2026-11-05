<script setup lang="ts">
import { computed } from "vue";
import { fieldDef, type SettingKey } from "../domain/settingsSchema";
import SettingRow from "../settings/SettingRow.vue";
import { provideSettingAccess } from "../settings/useSettingAccess";

/**
 * Normal mode's short form (language, diarization, formats; model and speaker count behind
 * "details"). It edits the ONE file Normal shows; Expert's categories never appear here.
 */
const props = withDefaults(defineProps<{ inputId: string | null; fields: SettingKey[] }>(), {});
provideSettingAccess(() => "normal", () => props.inputId);
const defs = computed(() => props.fields.map((k) => fieldDef(k)));
</script>

<template>
  <div class="p-fields--compact" data-testid="settings-fields-normal">
    <SettingRow v-for="def in defs" :key="def.key" :def="def" simple />
  </div>
</template>
