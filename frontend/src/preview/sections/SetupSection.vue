<script setup lang="ts">
import { mdiAlertOctagonOutline, mdiCheckAll, mdiInformationOutline, mdiRadar, mdiWrenchOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import ProfileCard from "../environment/ProfileCard.vue";
import { useEnvironment } from "../environment/useEnvironment";
import SetupNotice from "../wizard/SetupNotice.vue";
import SetupWizard from "../wizard/SetupWizard.vue";
import { useSetupNav } from "../wizard/useSetupNav";

/**
 * Execution environment with FICTIONAL information (switch the example in the review strip).
 * Iteration 4 (owner memos #16/#23): "Re-detect" only refreshes this view; "Set up…" installs,
 * adds or repairs (it opens the wizard, shown here in place of the page). Cards offer "Set up…" /
 * "Repair…" only where something is missing or broken; a working profile is never reinstalled.
 * Nothing is detected or verified; B4 (backend) stays the source of truth.
 */
const { demo, layout, layoutState, setupState, t } = usePreview();
const { view, primary, checking } = useEnvironment();
const { openSetup } = useSetupNav();
const expert = computed(() => layoutState.value.mode === "expert");
const open = ref<Record<string, boolean>>({});
const profiles = computed(() => (expert.value ? view.value.profiles : primary.value ? [primary.value] : []));
const isOpen = (name: string) => open.value[name] ?? false;
</script>

<template>
  <SetupWizard v-if="setupState.wizardOpen" />
  <section v-else class="u-page p-page-wide u-stack" aria-labelledby="setup-heading" data-testid="section-setup">
    <div class="u-row">
      <h1 id="setup-heading" class="u-heading">{{ t(expert ? "setup.titleExpert" : "section.setup") }}</h1>
      <v-spacer />
      <v-btn variant="tonal" :prepend-icon="mdiRadar" :disabled="checking" data-testid="env-redetect" @click="demo.redetect()">{{ t("env.redetect") }}</v-btn>
      <v-btn variant="tonal" color="primary" :prepend-icon="mdiWrenchOutline" data-testid="env-setup" @click="openSetup('add')">{{ t("env.setup") }}</v-btn>
      <v-btn variant="text" data-testid="env-maintenance" @click="layout.openDialog('maintenance')">{{ t("menu.tools.maintenance") }}</v-btn>
    </div>
    <SetupNotice />
    <div class="p-feedback" role="note" data-testid="env-demo-note">
      <v-icon :icon="mdiInformationOutline" size="small" color="warning" aria-hidden="true" />
      <span>{{ t("env.demoNote") }}</span>
    </div>

    <div v-if="checking" role="status" aria-live="polite" class="u-stack" data-testid="env-checking">
      <v-skeleton-loader type="list-item-three-line" />
      <span class="text-body-medium">{{ t("env.checking") }}</span>
      <div class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
        <v-chip label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
        <v-btn variant="outlined" :prepend-icon="mdiCheckAll" data-testid="env-finish" @click="demo.finishDetect()">{{ t("env.finish") }}</v-btn>
      </div>
    </div>
    <v-alert v-else-if="view.loadFailed" type="error" role="alert" :icon="mdiAlertOctagonOutline" :title="t('env.loadFailedTitle')" data-testid="env-load-failed">
      <p>{{ t("env.loadFailedBody") }}</p>
      <p class="text-body-small">{{ t("area.errorCode", { code: "demo_env_unavailable" }) }}</p>
    </v-alert>
    <template v-else>
      <h2 v-if="expert" class="u-subheading">{{ t("env.candidates", { count: profiles.length }) }}</h2>
      <div class="p-profiles" :class="{ 'p-profiles--single': !expert }" data-testid="env-profiles">
        <ProfileCard v-for="p in profiles" :key="p.name" :profile="p" :brief="!expert" :expanded="isOpen(p.name)" @toggle="open = { ...open, [p.name]: !isOpen(p.name) }" />
      </div>
    </template>
  </section>
</template>
