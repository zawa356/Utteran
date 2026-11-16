<script setup lang="ts">
import { useI18n } from "../../composables/core";

/**
 * In v1 mode (S5-A) there is no multi-file start: the atomic B7 batch is S5-B. The button is shown
 * disabled with the reason; no other way of sending several files exists in this mode.
 */
const { t } = useI18n();
</script>

<template>
  <section class="u-stack" aria-labelledby="v1-batch-heading" data-testid="v1-batch-disabled">
    <h2 id="v1-batch-heading" class="u-subheading">{{ t("tx.batch.title") }}</h2>
    <v-alert type="info" density="compact">{{ t("v1.batch.disabled") }}</v-alert>
    <div class="u-row">
      <v-btn color="primary" disabled data-testid="v1-batch-start">{{ t("v1.batch.button") }}</v-btn>
    </div>
  </section>
</template>
