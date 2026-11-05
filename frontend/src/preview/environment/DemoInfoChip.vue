<script setup lang="ts">
import { usePreview } from "../composables";

/** Shown next to every piece of the fictional environment information. */
const { t } = usePreview();
</script>

<template>
  <v-chip size="x-small" label color="warning" variant="outlined" data-testid="env-demo-chip">{{ t("env.demoChip") }}</v-chip>
</template>
