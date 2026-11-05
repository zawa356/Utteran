<script setup lang="ts">
import { mdiOpenInNew } from "@mdi/js";
import AppIcon from "../components/AppIcon.vue";
import { usePreview } from "../composables";
import { ABOUT } from "../domain/about";

const { t } = usePreview();
</script>

<template>
  <div class="u-stack" data-testid="about-view">
    <div class="u-row">
      <AppIcon :size="48" />
      <span class="text-h6 font-weight-bold">{{ ABOUT.name }}</span>
      <v-chip label color="warning" variant="outlined" data-testid="about-dev-chip">{{ t("dialog.about.devChip") }}</v-chip>
    </div>
    <dl class="p-dl text-body-medium" data-testid="about-facts">
      <dt>{{ t("dialog.about.version") }}</dt>
      <dd data-testid="about-version">{{ t("dialog.about.versionValue", { version: ABOUT.version }) }}</dd>
      <dt>{{ t("dialog.about.license") }}</dt>
      <dd>{{ ABOUT.license }}</dd>
      <dt>{{ t("dialog.about.links") }}</dt>
      <dd>
        <ul class="p-bullets">
          <li><a :href="ABOUT.repository" target="_blank" rel="noopener noreferrer" data-testid="about-repo">{{ t("dialog.about.repo") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
          <li><a :href="ABOUT.licenseUrl" target="_blank" rel="noopener noreferrer">{{ t("dialog.about.licenseText") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
          <li><a :href="ABOUT.noticesUrl" target="_blank" rel="noopener noreferrer">{{ t("dialog.about.notices") }} <v-icon :icon="mdiOpenInNew" size="small" aria-hidden="true" /></a></li>
        </ul>
      </dd>
    </dl>
    <p class="text-body-small">{{ t("dialog.about.note") }}</p>
  </div>
</template>
