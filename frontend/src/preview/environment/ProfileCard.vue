<script setup lang="ts">
import { mdiAlertOutline, mdiCheck, mdiChevronDown, mdiChevronUp } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import type { EnvAction, EnvProfile } from "../domain/envExamples";
import type { WizardChoices, WizardEntry } from "../domain/setup";
import { type PreviewKey } from "../i18n";
import { useSetupNav } from "../wizard/useSetupNav";
import EnvStatusChip from "./EnvStatusChip.vue";
import { useEnvironment } from "./useEnvironment";

/**
 * One fictional profile. Status line and key facts on top; what can be done about it as BUTTONS
 * at the bottom ("Set up…", "Repair…", …) — only when something is missing or broken.
 * "Details" (Expert) holds the detection results and warnings; it never mixes "a GPU was detected"
 * with "this profile is ready" (separate lines: detection vs. status).
 */
const props = withDefaults(defineProps<{ profile: EnvProfile; expanded: boolean; brief?: boolean }>(), { brief: false });
const emit = defineEmits<{ toggle: [] }>();
const { t } = usePreview();
const { value } = useEnvironment();
const { openSetup } = useSetupNav();
const BRIEF = new Set(["asr", "model", "device", "runtime", "diarization"]);
const facts = computed(() => (props.brief ? props.profile.facts.filter((f) => BRIEF.has(f.label)) : props.profile.facts));
const hasDetails = computed(() => props.profile.detection.length + props.profile.warnings.length > 0);
const ACTION: Record<EnvAction, { entry: WizardEntry; preset: Partial<WizardChoices>; key: PreviewKey }> = {
  setup: { entry: "first_run", preset: {}, key: "env.action.setup" },
  addGpu: { entry: "add", preset: { device: "gpu" }, key: "env.action.addGpu" },
  repair: { entry: "repair", preset: {}, key: "env.action.repair" },
  diarization: { entry: "add", preset: { diarization: true }, key: "env.action.diarization" },
};
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
      <ul v-if="brief && profile.warnings.length > 0" class="p-bullets" data-testid="env-brief-notes">
        <li v-for="w in profile.warnings" :key="w"><v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" /> {{ t(w as PreviewKey) }}</li>
      </ul>
      <div v-if="profile.actions.length > 0 || (!brief && hasDetails)" class="u-row">
        <v-btn
          v-for="a in profile.actions"
          :key="a"
          :variant="a === 'repair' || a === 'setup' ? 'flat' : 'tonal'"
          color="primary"
          :data-testid="`env-action-${a}-${profile.name}`"
          @click="openSetup(ACTION[a].entry, ACTION[a].preset)"
        >
          {{ t(ACTION[a].key) }}
        </v-btn>
        <v-spacer />
        <v-btn v-if="!brief && hasDetails" variant="text" :append-icon="expanded ? mdiChevronUp : mdiChevronDown" :aria-expanded="expanded" :data-testid="`env-toggle-${profile.name}`" @click="emit('toggle')">
          {{ expanded ? t("env.hideDetails") : t("env.showDetails") }}
        </v-btn>
      </div>
      <div v-if="!brief && expanded" class="u-stack" :data-testid="`env-details-${profile.name}`">
        <div v-if="profile.detection.length > 0">
          <div class="text-body-medium font-weight-medium">{{ t("env.detectionTitle") }}</div>
          <ul class="p-bullets">
            <li v-for="e in profile.detection" :key="e"><v-icon :icon="mdiCheck" size="small" aria-hidden="true" /> {{ t(e as PreviewKey) }}</li>
          </ul>
        </div>
        <div v-if="profile.warnings.length > 0">
          <div class="text-body-medium font-weight-medium">{{ t("env.warningsTitle") }}</div>
          <ul class="p-bullets">
            <li v-for="w in profile.warnings" :key="w"><v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" /> {{ t(w as PreviewKey) }}</li>
          </ul>
        </div>
      </div>
    </div>
  </v-card>
</template>
