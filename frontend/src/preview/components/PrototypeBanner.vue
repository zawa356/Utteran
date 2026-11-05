<script setup lang="ts">
import { usePreview } from "../composables";

/** Always visible: this page is a prototype with synthetic data that processes nothing. */
const { t } = usePreview();
</script>

<template>
  <v-alert type="warning" density="compact" class="ma-2" role="note" :title="t('banner.title')" data-testid="prototype-banner">
    {{ t("banner.body") }}
  </v-alert>
</template>
