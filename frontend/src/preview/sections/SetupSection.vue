<script setup lang="ts">
import { mdiAlertOctagonOutline, mdiArrowCollapseVertical, mdiArrowExpandVertical, mdiCheckAll, mdiRadar } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import DemoInfoChip from "../environment/DemoInfoChip.vue";
import ProfileCard from "../environment/ProfileCard.vue";
import { useEnvironment } from "../environment/useEnvironment";

/**
 * Execution environment & setup with FICTIONAL information (switch the example in the review
 * strip). Normal: the recommended profile only, details on demand. Expert: every profile, with
 * density and expand/collapse controls. Nothing is detected; B4 (backend) stays the source of truth.
 */
const { demo, layoutState, t } = usePreview();
const { view, primary, checking } = useEnvironment();
const expert = computed(() => layoutState.value.mode === "expert");
const dense = ref(false);
const open = ref<Record<string, boolean>>({});
const profiles = computed(() => (expert.value ? view.value.profiles : primary.value ? [primary.value] : []));
const isOpen = (name: string) => open.value[name] ?? false;
function setAll(value: boolean) {
  open.value = Object.fromEntries(view.value.profiles.map((p) => [p.name, value]));
}
</script>

<template>
  <section class="u-page p-page-wide u-stack" aria-labelledby="setup-heading" data-testid="section-setup">
    <div class="u-row">
      <h1 id="setup-heading" class="u-heading">{{ t(expert ? "setup.titleExpert" : "section.setup") }}</h1>
      <DemoInfoChip />
    </div>
    <v-alert type="warning" variant="outlined" data-testid="env-demo-note">{{ t("env.demoNote") }}</v-alert>

    <div class="u-row">
      <v-btn size="small" variant="tonal" :prepend-icon="mdiRadar" :disabled="checking" data-testid="env-redetect" @click="demo.redetect()">{{ t("env.redetect") }}</v-btn>
      <template v-if="expert && !checking && !view.loadFailed">
        <v-btn-toggle :model-value="dense ? 'dense' : 'comfortable'" mandatory density="compact" variant="outlined" divided :aria-label="t('env.density')" data-testid="env-density" @update:model-value="(v: string) => (dense = v === 'dense')">
          <v-btn value="comfortable" size="small">{{ t("env.densityComfortable") }}</v-btn>
          <v-btn value="dense" size="small" data-testid="env-density-dense">{{ t("env.densityDense") }}</v-btn>
        </v-btn-toggle>
        <v-btn size="small" variant="text" :prepend-icon="mdiArrowExpandVertical" data-testid="env-expand-all" @click="setAll(true)">{{ t("env.expandAll") }}</v-btn>
        <v-btn size="small" variant="text" :prepend-icon="mdiArrowCollapseVertical" @click="setAll(false)">{{ t("env.collapseAll") }}</v-btn>
      </template>
    </div>

    <div v-if="checking" role="status" aria-live="polite" class="u-stack" data-testid="env-checking">
      <v-skeleton-loader type="list-item-three-line" />
      <span class="text-body-medium">{{ t("env.checking") }}</span>
      <div class="p-demo u-row" role="group" :aria-label="t('demo.controls')">
        <v-chip size="x-small" label color="warning" variant="outlined">{{ t("demo.chip") }}</v-chip>
        <v-btn size="small" variant="outlined" :prepend-icon="mdiCheckAll" data-testid="env-finish" @click="demo.finishDetect()">{{ t("env.finish") }}</v-btn>
      </div>
    </div>
    <v-alert v-else-if="view.loadFailed" type="error" role="alert" :icon="mdiAlertOctagonOutline" :title="t('env.loadFailedTitle')" data-testid="env-load-failed">
      <p>{{ t("env.loadFailedBody") }}</p>
      <p class="text-body-small">{{ t("area.errorCode", { code: "demo_env_unavailable" }) }}</p>
    </v-alert>
    <div v-else class="p-profiles" :class="{ 'p-profiles--single': !expert }" data-testid="env-profiles">
      <ProfileCard v-for="p in profiles" :key="p.name" :profile="p" :expanded="isOpen(p.name)" :dense="dense" @toggle="open = { ...open, [p.name]: !isOpen(p.name) }" />
    </div>
    <p v-if="!expert && view.profiles.length > 1" class="text-body-small" data-testid="env-more-in-expert">{{ t("env.moreInExpert", { count: view.profiles.length - 1 }) }}</p>
    <p class="text-body-small">{{ t("setup.later") }}</p>
  </section>
</template>
