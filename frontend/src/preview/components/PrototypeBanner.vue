<script setup lang="ts">
import { mdiAlertOutline } from "@mdi/js";
import { usePreview } from "../composables";

/**
 * The one shared prototype notice (iteration 3): a single short line at the top of every page.
 * Product screens below it carry no further demo disclaimers; the review strip at the bottom
 * holds the reviewer controls.
 */
const { t } = usePreview();
</script>

<template>
  <div class="p-banner" role="note" data-testid="prototype-banner">
    <v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" />
    <span><strong>{{ t("banner.title") }}</strong> {{ t("banner.body") }}</span>
  </div>
</template>
