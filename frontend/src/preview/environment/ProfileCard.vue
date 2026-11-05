<script setup lang="ts">
import { mdiAlertOutline, mdiCheck, mdiChevronDown, mdiChevronUp, mdiWrenchOutline } from "@mdi/js";
import { usePreview } from "../composables";
import type { EnvProfile } from "../domain/envExamples";
import { type PreviewKey } from "../i18n";
import DemoInfoChip from "./DemoInfoChip.vue";
import EnvStatusChip from "./EnvStatusChip.vue";
import { useEnvironment } from "./useEnvironment";

/** One fictional profile: key facts, then (expanded) evidence, warnings and needed actions. */
defineProps<{ profile: EnvProfile; expanded: boolean; dense: boolean }>();
const emit = defineEmits<{ toggle: [] }>();
const { t } = usePreview();
const { value } = useEnvironment();
</script>

<template>
  <v-card tag="section" :aria-label="t(`env.profile.${profile.name}`)" :data-testid="`env-profile-${profile.name}`">
    <div class="p-profile" :class="{ 'p-profile--dense': dense }">
      <div class="u-row">
        <h3 class="text-body-large font-weight-medium">{{ t(`env.profile.${profile.name}`) }}</h3>
        <v-chip v-if="profile.recommended" size="x-small" label color="primary" variant="tonal" data-testid="env-recommended">{{ t("env.recommended") }}</v-chip>
        <EnvStatusChip :status="profile.status" />
        <DemoInfoChip />
      </div>
      <dl class="p-dl text-body-medium">
        <template v-for="f in profile.facts" :key="f.label">
          <dt>{{ t(`env.fact.${f.label}`) }}</dt>
          <dd :data-testid="`env-fact-${f.label}`">{{ value(f) }}</dd>
        </template>
      </dl>
      <v-btn size="small" variant="text" :append-icon="expanded ? mdiChevronUp : mdiChevronDown" :aria-expanded="expanded" :data-testid="`env-toggle-${profile.name}`" @click="emit('toggle')">
        {{ expanded ? t("env.hideEvidence") : t("env.showEvidence") }}
      </v-btn>
      <div v-if="expanded" class="u-stack" :data-testid="`env-evidence-${profile.name}`">
        <div>
          <div class="text-body-small font-weight-medium">{{ t("env.evidenceTitle") }}</div>
          <ul class="p-bullets">
            <li v-for="e in profile.evidence" :key="e"><v-icon :icon="mdiCheck" size="x-small" aria-hidden="true" /> {{ t(e as PreviewKey) }}</li>
          </ul>
        </div>
        <div v-if="profile.warnings.length > 0">
          <div class="text-body-small font-weight-medium">{{ t("env.warningsTitle") }}</div>
          <ul class="p-bullets">
            <li v-for="w in profile.warnings" :key="w"><v-icon :icon="mdiAlertOutline" size="x-small" color="warning" aria-hidden="true" /> {{ t(w as PreviewKey) }}</li>
          </ul>
        </div>
        <div v-if="profile.actions.length > 0">
          <div class="text-body-small font-weight-medium">{{ t("env.actionsTitle") }}</div>
          <ul class="p-bullets">
            <li v-for="a in profile.actions" :key="a"><v-icon :icon="mdiWrenchOutline" size="x-small" aria-hidden="true" /> {{ t(a as PreviewKey) }}</li>
          </ul>
        </div>
      </div>
    </div>
  </v-card>
</template>
