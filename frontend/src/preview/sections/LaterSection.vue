<script setup lang="ts">
import { usePreview } from "../composables";

/** Models: designed later (S4). Shows the planned content, no operable controls. */
const { t } = usePreview();
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="models-heading" data-testid="section-models">
    <h1 id="models-heading" class="u-heading">{{ t("section.models") }}</h1>
    <v-alert type="info" :title="t('later.title')" data-testid="later-notice">
      <p>{{ t("later.models") }}</p>
      <p class="text-body-small">{{ t("later.stage", { stage: "UI-3 S4" }) }}</p>
    </v-alert>
  </section>
</template>
