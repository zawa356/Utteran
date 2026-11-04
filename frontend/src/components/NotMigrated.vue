<script setup lang="ts">
import { useI18n } from "../composables/core";

defineProps<{ section: string; stage?: string; body?: string }>();
const { t } = useI18n();
</script>

<template>
  <v-alert type="info" :title="t('notMigrated.title')" data-testid="not-migrated">
    <p>{{ body ?? t("notMigrated.body", { section }) }}</p>
    <p v-if="stage" class="text-caption">{{ t("notMigrated.stage", { stage }) }}</p>
  </v-alert>
</template>
