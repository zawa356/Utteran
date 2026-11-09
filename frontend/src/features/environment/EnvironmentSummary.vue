<script setup lang="ts">
import { computed } from "vue";
import type { LegacyEnvironment } from "../../api/legacy";
import { useI18n } from "../../composables/core";
import { summarize } from "../../domain/environmentSummary";

/** The legacy environment strip's four items. Text bindings only (no v-html). */
const props = defineProps<{ data: LegacyEnvironment | null }>();
const { t } = useI18n();
const summary = computed(() => summarize(props.data));

const items = computed(() => {
  const s = summary.value;
  const missing = (kind: string) => (kind === "not_obtained" ? t("env.notObtained") : kind === "unknown" ? t("env.unknown") : t("env.none"));
  const hardware =
    s.hardware.kind === "gpu"
      ? [s.hardware.names.join(", "), s.hardware.more ? t("env.sum.more", { count: s.hardware.more }) : ""].filter(Boolean).join(" ")
      : s.hardware.kind === "cpu"
        ? s.hardware.threads === null
          ? t("env.sum.cpuUnknown")
          : t("env.sum.cpu", { threads: s.hardware.threads })
        : missing(s.hardware.kind);
  return [
    { id: "hardware", label: t("env.sum.hardware"), value: hardware, absent: s.hardware.kind === "not_obtained" },
    { id: "profile", label: t("env.sum.profile"), value: s.profile.kind === "value" ? s.profile.name : missing(s.profile.kind), absent: s.profile.kind !== "value" },
    { id: "models", label: t("env.sum.models"), value: s.models.kind === "count" ? t("env.sum.count", { count: s.models.count }) : missing(s.models.kind), absent: s.models.kind !== "count" },
    { id: "native", label: t("env.sum.native"), value: s.native.kind === "variants" ? s.native.names.join(", ") : missing(s.native.kind), absent: s.native.kind !== "variants" },
  ];
});
</script>

<template>
  <dl class="u-env-summary" data-testid="env-summary">
    <div v-for="item in items" :key="item.id" class="u-env-summary__item" :data-testid="`env-sum-${item.id}`">
      <dt class="text-caption">{{ item.label }}</dt>
      <dd :class="item.absent ? 'text-medium-emphasis' : 'font-weight-medium'">{{ item.value }}</dd>
    </div>
  </dl>
</template>
