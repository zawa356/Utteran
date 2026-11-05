<script setup lang="ts">
import { mdiUndoVariant } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { selectionField } from "../domain/demo";
import type { SettingKey } from "../domain/fixtures";

/**
 * One Inspector row. In "selection" mode it shows where the value comes from (inherited from
 * the common defaults / individual / partly individual), an "individual" switch and "back to
 * common". The control slot is enabled only when the value is individually set for all selected
 * inputs, so an inherited value can never be edited by accident.
 */
const props = defineProps<{ field: SettingKey; label: string; mode: "common" | "selection" | "normal" }>();
const { demo, state, t } = usePreview();
const view = computed(() => selectionField(state.value, props.field));
const editable = computed(() => props.mode !== "selection" || view.value.coverage === "all");
const sourceText = computed(() => {
  if (props.mode !== "selection") return t("inspector.source.common");
  return t(`inspector.source.${view.value.coverage}`);
});
function onOverride(on: boolean | null) {
  if (on) demo.startOverride(props.field);
  else demo.resetOverride(props.field);
}
</script>

<template>
  <div class="p-field" :data-testid="`field-${field}`">
    <div class="p-field__head">
      <span class="text-body-medium font-weight-medium">{{ label }}</span>
      <v-chip
        v-if="mode === 'selection'"
        label
        :color="view.coverage === 'none' ? undefined : 'primary'"
        :variant="view.coverage === 'none' ? 'outlined' : 'tonal'"
        :data-testid="`field-${field}-source`"
      >
        {{ sourceText }}
      </v-chip>
      <v-spacer />
      <template v-if="mode === 'selection'">
        <v-switch
          :model-value="view.coverage === 'all'"
          :label="t('inspector.individual')"
          color="primary"
          hide-details
          class="flex-grow-0"
          :data-testid="`field-${field}-override`"
          @update:model-value="onOverride"
        />
        <v-btn
          v-if="view.coverage !== 'none'"
          variant="text"
          :prepend-icon="mdiUndoVariant"
          :data-testid="`field-${field}-reset`"
          @click="demo.resetOverride(field)"
        >
          {{ t("inspector.backToCommon") }}
        </v-btn>
      </template>
    </div>
    <slot :disabled="!editable" :mixed="mode === 'selection' && view.mixed" />
    <div v-if="mode === 'selection' && view.mixed" class="text-body-small" :data-testid="`field-${field}-mixed`">{{ t("inspector.mixed") }}</div>
  </div>
</template>
