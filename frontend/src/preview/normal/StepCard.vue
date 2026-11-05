<script setup lang="ts">
/** A numbered step of the Normal flow (plain outlined card, heading + body). */
defineProps<{ n: number; title: string; testid: string }>();
</script>

<template>
  <v-card class="pa-3" :aria-labelledby="`${testid}-heading`" tag="section" :data-testid="testid">
    <div class="u-row mb-2">
      <span class="p-step-n" aria-hidden="true">{{ n }}</span>
      <h2 :id="`${testid}-heading`" class="u-subheading">{{ title }}</h2>
      <v-spacer />
      <slot name="actions" />
    </div>
    <slot />
  </v-card>
</template>
