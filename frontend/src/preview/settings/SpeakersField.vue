<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { type ProcessingSettings, SPEAKER_MODES } from "../domain/settingsSchema";
import { useSettingAccess } from "./useSettingAccess";

/**
 * Speaker count as ONE choice — automatic, a fixed number, or a range — so a fixed count and a
 * range can never be set at the same time (the contract rejects that). `simple` (Normal mode):
 * one select with "automatic / n people"; a range set in Expert is shown and kept.
 */
defineProps<{ disabled: boolean; simple?: boolean; hint?: string }>();
const access = useSettingAccess();
const { t } = usePreview();
const mode = computed(() => access.value("speakerMode"));
const mixed = computed(() => access.mixed("speakerMode") || access.mixed("numSpeakers") || access.mixed("minSpeakers") || access.mixed("maxSpeakers"));
const RANGE = "range";
const simpleItems = computed(() => {
  const items: { value: string; title: string }[] = [{ value: "auto", title: t("speakers.auto") }];
  for (let n = 1; n <= 6; n += 1) items.push({ value: String(n), title: t("speakers.n", { n }) });
  if (mode.value === "range") items.push({ value: RANGE, title: t("speakers.range", { min: access.value("minSpeakers"), max: access.value("maxSpeakers") }) });
  return items;
});
const simpleValue = computed(() => (mixed.value ? null : mode.value === "auto" ? "auto" : mode.value === "range" ? RANGE : String(access.value("numSpeakers"))));
function setSimple(v: string | null) {
  if (!v || v === RANGE) return;
  if (v === "auto") access.setPatch({ speakerMode: "auto" });
  else access.setPatch({ speakerMode: "fixed", numSpeakers: Number(v) });
}
function setMode(v: ProcessingSettings["speakerMode"] | null) {
  if (v) access.setPatch({ speakerMode: v });
}
function setCount(key: "numSpeakers" | "minSpeakers" | "maxSpeakers", text: string) {
  const n = Number(text);
  if (text.trim() !== "" && Number.isFinite(n)) access.setPatch({ [key]: n });
}
const modes = computed(() => SPEAKER_MODES.map((m) => ({ value: m, title: t(`opt.speakerMode.${m}`) })));
</script>

<template>
  <v-select
    v-if="simple"
    :items="simpleItems"
    :model-value="simpleValue"
    :placeholder="t('inspector.mixedShort')"
    :disabled="disabled"
    :hint="hint"
    :persistent-hint="hint !== undefined"
    :aria-label="t('field.speakerMode')"
    :data-testid="`input-speakers-${access.mode}`"
    @update:model-value="setSimple"
  />
  <div v-else class="u-stack" :data-testid="`input-speakers-${access.mode}`">
    <v-btn-toggle
      :model-value="mixed ? null : mode"
      mandatory
      divided
      variant="outlined"
      density="compact"
      color="primary"
      :disabled="disabled"
      :aria-label="t('field.speakerMode')"
      data-testid="speakers-mode"
      @update:model-value="setMode"
    >
      <v-btn v-for="m in modes" :key="m.value" :value="m.value" :data-testid="`speakers-mode-${m.value}`">{{ m.title }}</v-btn>
    </v-btn-toggle>
    <div v-if="!mixed && mode === 'fixed'" class="p-numbers">
      <v-text-field
        :model-value="String(access.value('numSpeakers'))"
        type="number"
        min="1"
        max="100"
        :label="t('field.numSpeakers')"
        :disabled="disabled"
        :error-messages="access.issueText('numSpeakers')"
        hide-details="auto"
        data-testid="speakers-num"
        @update:model-value="(v) => setCount('numSpeakers', v)"
      />
    </div>
    <div v-else-if="!mixed && mode === 'range'" class="p-numbers">
      <v-text-field
        :model-value="String(access.value('minSpeakers'))"
        type="number"
        min="1"
        max="100"
        :label="t('field.minSpeakers')"
        :disabled="disabled"
        :error-messages="access.issueText('minSpeakers')"
        hide-details="auto"
        data-testid="speakers-min"
        @update:model-value="(v) => setCount('minSpeakers', v)"
      />
      <v-text-field
        :model-value="String(access.value('maxSpeakers'))"
        type="number"
        min="1"
        max="100"
        :label="t('field.maxSpeakers')"
        :disabled="disabled"
        :error-messages="access.issueText('maxSpeakers')"
        hide-details="auto"
        data-testid="speakers-max"
        @update:model-value="(v) => setCount('maxSpeakers', v)"
      />
    </div>
    <p v-if="hint" class="text-body-small">{{ hint }}</p>
  </div>
</template>
