<script setup lang="ts">
import { useI18n } from "../../composables/core";
import EnvironmentPanel from "../environment/EnvironmentPanel.vue";

const { t } = useI18n();
</script>

<template>
  <section class="u-page u-stack" aria-labelledby="transcribe-heading" data-testid="normal-workspace">
    <h1 id="transcribe-heading" class="u-heading">{{ t("transcribe.title") }}</h1>
    <v-alert type="info" data-testid="transcribe-not-migrated">{{ t("transcribe.notMigrated") }}</v-alert>
    <v-card class="pa-3"><EnvironmentPanel /></v-card>
  </section>
</template>
