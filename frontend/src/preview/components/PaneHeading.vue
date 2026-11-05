<script setup lang="ts">
/**
 * Expert pane heading (iteration 3): a numbered step, a short name and an optional short status
 * on the right ("選択中: 3件"). The three panes share size, spacing and accent so the left → right
 * direction (files → settings → queue) reads without arrows or a wizard.
 */
defineProps<{ step: number; title: string; meta?: string; id: string }>();
</script>

<template>
  <div class="p-panehead">
    <span class="p-panehead__step" aria-hidden="true">{{ step }}</span>
    <h2 :id="id" class="p-panehead__title">{{ title }}</h2>
    <span v-if="meta" class="p-panehead__meta" :data-testid="`${id}-meta`">{{ meta }}</span>
    <slot />
  </div>
</template>
