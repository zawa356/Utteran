<script setup lang="ts">
import { mdiAlertOutline } from "@mdi/js";
import { usePreview } from "../composables";

/** Always visible, one slim line: this page is a prototype with synthetic data that processes nothing. */
const { t } = usePreview();
</script>

<template>
  <div class="p-banner" role="note" data-testid="prototype-banner">
    <v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" />
    <strong class="text-body-medium font-weight-bold">{{ t("banner.title") }}</strong>
    <span class="text-body-small">{{ t("banner.body") }}</span>
  </div>
</template>
