<script setup lang="ts">
import { mdiAlertOctagonOutline, mdiCheckAll, mdiInformationOutline, mdiRadar } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import ProfileCard from "../environment/ProfileCard.vue";
import { useEnvironment } from "../environment/useEnvironment";

/**
 * Execution environment with FICTIONAL information (switch the example in the review strip).
 * Iteration 3 (owner memo #13): one readable layout — no density switch, no expand/collapse-all.
 * Normal: the recommended profile and whether it can run, in brief. Expert: every candidate with
 * its technical evidence behind "details". The fact that everything here is fictional is stated
 * once, at the top. Nothing is detected or verified; B4 (backend) stays the source of truth.
 */
const { demo, layoutState, t } = usePreview();
const { view, primary, checking } = useEnvironment();
const expert = computed(() => layoutState.value.mode === "expert");
const open = ref<Record<string, boolean>>({});
const profiles = computed(() => (expert.value ? view.value.profiles : primary.value ? [primary.value] : []));
const isOpen = (name: string) => open.value[name] ?? false;
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="setup-heading" data-testid="section-setup">
    <div class="u-row">
      <h1 id="setup-heading" class="u-heading">{{ t(expert ? "setup.titleExpert" : "section.setup") }}</h1>
      <v-spacer />
      <v-btn variant="tonal" :prepend-icon="mdiRadar" :disabled="checking" data-testid="env-redetect" @click="demo.redetect()">{{ t("env.redetect") }}</v-btn>
    </div>
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
