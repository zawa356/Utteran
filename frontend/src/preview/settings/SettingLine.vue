<script setup lang="ts">
/**
 * One app-setting line (#25): a label column and the control, with a fixed 16 px gap. When the
 * page is narrow (container query) the label moves above the control with a 8 px gap; the two
 * never touch, in Japanese or English.
 */
defineProps<{ label: string; id: string }>();
</script>

<template>
  <div class="p-setline" :data-testid="`setline-${id}`">
    <span :id="`setline-label-${id}`" class="p-setline__label text-body-medium font-weight-medium">{{ label }}</span>
    <div class="p-setline__control"><slot :labelledby="`setline-label-${id}`" /></div>
  </div>
</template>
