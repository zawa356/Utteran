<script setup lang="ts">
import { computed } from "vue";
import type { EnvironmentIssue, LegacyEnvironment } from "../../api/legacy";
import { useI18n } from "../../composables/core";
import type { MessageKey } from "../../i18n";
import { rebuildNeeded } from "../../domain/environmentSummary";

/**
 * Guidance, rebuild warnings and probe failures, in fixed UI-language text. Raw server error
 * strings are never kept (they can carry paths), so nothing here repeats them.
 */
const props = defineProps<{ data: LegacyEnvironment }>();
const { t } = useI18n();

function issueText(issue: EnvironmentIssue): string {
  const probe = t(`env.probeName.${issue.probe ?? "unknown"}` as MessageKey);
  if (issue.kind === "timeout") return t("env.issue.timeout", { probe, seconds: issue.seconds ?? "?" });
  return t(issue.kind === "malformed" ? "env.issue.malformed" : "env.issue.failed", { probe });
}

const rebuild = computed(() =>
  rebuildNeeded(props.data).map((p) => t("env.rebuild", { profile: p.name, reason: t(`env.reason.${p.compatibilityReason ?? "other"}` as MessageKey) })),
);
const otherWarnings = computed(() => Math.max(0, props.data.profileWarningCount - rebuild.value.length));
const issues = computed(() => props.data.issues.map(issueText));
const guidance = computed(() => props.data.guidance.map((g) => t(`env.guidance.${g}` as MessageKey)));
const empty = computed(() => !rebuild.value.length && !otherWarnings.value && !issues.value.length && !guidance.value.length);
</script>

<template>
  <div class="u-stack" data-testid="env-notices">
    <p v-if="empty" class="text-body-2">{{ t("env.detail.noNotices") }}</p>
    <ul v-if="issues.length" class="u-env-list" data-testid="env-issues">
      <li v-for="(text, i) in issues" :key="`i${i}`">{{ text }}</li>
    </ul>
    <ul v-if="rebuild.length || otherWarnings" class="u-env-list" data-testid="env-rebuild">
      <li v-for="(text, i) in rebuild" :key="`r${i}`">{{ text }}</li>
      <li v-if="otherWarnings">{{ t("env.otherWarnings", { count: otherWarnings }) }}</li>
    </ul>
    <ul v-if="guidance.length" class="u-env-list" data-testid="env-guidance">
      <li v-for="(text, i) in guidance" :key="`g${i}`">{{ text }}</li>
    </ul>
  </div>
</template>
