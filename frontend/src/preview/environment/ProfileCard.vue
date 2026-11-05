<script setup lang="ts">
import { mdiAlertOutline, mdiCheck, mdiChevronDown, mdiChevronUp, mdiWrenchOutline } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import type { EnvProfile } from "../domain/envExamples";
import { type PreviewKey } from "../i18n";
import EnvStatusChip from "./EnvStatusChip.vue";
import { useEnvironment } from "./useEnvironment";

/**
 * One fictional profile. `brief` (Normal): the key facts only (ASR, model, device). Expert: every
 * fact, and the evidence / warnings / needed actions behind "details" per profile (no global
 * expand-all or density switch — there are only a few candidates).
 */
const props = withDefaults(defineProps<{ profile: EnvProfile; expanded: boolean; brief?: boolean }>(), { brief: false });
const emit = defineEmits<{ toggle: [] }>();
const { t } = usePreview();
const { value } = useEnvironment();
const BRIEF = new Set(["asr", "model", "device", "runtime"]);
const facts = computed(() => (props.brief ? props.profile.facts.filter((f) => BRIEF.has(f.label)) : props.profile.facts));
const hasDetails = computed(() => props.profile.evidence.length + props.profile.warnings.length + props.profile.actions.length > 0);
</script>

<template>
  <v-card tag="section" :aria-label="t(`env.profile.${profile.name}`)" :data-testid="`env-profile-${profile.name}`">
    <div class="p-profile">
      <div class="u-row">
        <h3 class="text-body-large font-weight-medium">{{ t(`env.profile.${profile.name}`) }}</h3>
        <v-chip v-if="profile.recommended" label color="primary" variant="tonal" data-testid="env-recommended">{{ t("env.recommended") }}</v-chip>
        <EnvStatusChip :status="profile.status" />
      </div>
      <dl class="p-dl text-body-medium">
        <template v-for="f in facts" :key="f.label">
          <dt>{{ t(`env.fact.${f.label}`) }}</dt>
          <dd :data-testid="`env-fact-${f.label}`">{{ value(f) }}</dd>
        </template>
      </dl>
      <template v-if="brief">
        <ul v-if="profile.warnings.length + profile.actions.length > 0" class="p-bullets" data-testid="env-brief-notes">
          <li v-for="w in profile.warnings" :key="w"><v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" /> {{ t(w as PreviewKey) }}</li>
          <li v-for="a in profile.actions" :key="a"><v-icon :icon="mdiWrenchOutline" size="small" aria-hidden="true" /> {{ t(a as PreviewKey) }}</li>
        </ul>
      </template>
      <template v-else-if="hasDetails">
        <v-btn variant="text" :append-icon="expanded ? mdiChevronUp : mdiChevronDown" :aria-expanded="expanded" :data-testid="`env-toggle-${profile.name}`" @click="emit('toggle')">
          {{ expanded ? t("env.hideEvidence") : t("env.showEvidence") }}
        </v-btn>
        <div v-if="expanded" class="u-stack" :data-testid="`env-evidence-${profile.name}`">
          <div v-if="profile.evidence.length > 0">
            <div class="text-body-medium font-weight-medium">{{ t("env.evidenceTitle") }}</div>
            <ul class="p-bullets">
              <li v-for="e in profile.evidence" :key="e"><v-icon :icon="mdiCheck" size="small" aria-hidden="true" /> {{ t(e as PreviewKey) }}</li>
            </ul>
          </div>
          <div v-if="profile.warnings.length > 0">
            <div class="text-body-medium font-weight-medium">{{ t("env.warningsTitle") }}</div>
            <ul class="p-bullets">
              <li v-for="w in profile.warnings" :key="w"><v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" /> {{ t(w as PreviewKey) }}</li>
            </ul>
          </div>
          <div v-if="profile.actions.length > 0">
            <div class="text-body-medium font-weight-medium">{{ t("env.actionsTitle") }}</div>
            <ul class="p-bullets">
              <li v-for="a in profile.actions" :key="a"><v-icon :icon="mdiWrenchOutline" size="small" aria-hidden="true" /> {{ t(a as PreviewKey) }}</li>
            </ul>
          </div>
        </div>
      </template>
    </div>
  </v-card>
</template>
