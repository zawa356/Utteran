<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { isActive, overrideCount } from "../domain/demo";
import { type DemoInput, formatDuration } from "../domain/fixtures";

/** One row of the Expert input list: selection, format/duration, submit state and override mark. */
const props = defineProps<{ input: DemoInput }>();
const { demo, state, t } = usePreview();
const selected = computed(() => state.value.selected.includes(props.input.id));
const overrides = computed(() => overrideCount(state.value, props.input.id));
const status = computed(() => {
  if (!props.input.supported) return t("input.status.unsupported");
  const jobs = state.value.jobs.filter((j) => j.inputId === props.input.id);
  const last = jobs[jobs.length - 1];
  if (!last) return t("input.status.notSubmitted");
  if (isActive(last)) return t("input.status.submitted");
  return t(`job.state.${last.state}`);
});
</script>

<template>
  <v-list-item
    :active="selected"
    color="primary"
    :title="input.name"
    :aria-selected="selected"
    :data-testid="`input-row-${input.id}`"
    @click="demo.toggleSelect(input.id)"
  >
    <template #prepend>
      <v-checkbox-btn :model-value="selected" tabindex="-1" :aria-label="t('input.select', { name: input.name })" @click.stop="demo.toggleSelect(input.id)" />
    </template>
    <template #title>
      <span class="p-ellipsis" :title="input.name">{{ input.name }}</span>
    </template>
    <template #subtitle>
      <span>{{ input.format.toUpperCase() }} · {{ formatDuration(input.durationSec) }} · {{ status }}</span>
    </template>
    <template #append>
      <v-chip v-if="!input.supported" size="x-small" label color="warning" variant="tonal">{{ t("input.unsupported") }}</v-chip>
      <v-chip v-else-if="overrides > 0" size="x-small" label color="primary" variant="tonal" :data-testid="`input-row-${input.id}-override`">
        {{ t("input.overrides", { count: overrides }) }}
      </v-chip>
    </template>
  </v-list-item>
</template>
