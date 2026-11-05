<script setup lang="ts">
// The existing Utteran icon (icon/utteran.svg). Vite's dev server may not serve files outside
// frontend/, so the prototype keeps a byte-identical copy; preview/appIcon.test.ts fails if the
// two ever differ. No new icon is drawn here. Decorative next to the visible name "Utteran".
import iconUrl from "../assets/utteran.svg";

withDefaults(defineProps<{ size?: number }>(), { size: 22 });
</script>

<template>
  <img :src="iconUrl" :width="size" :height="size" alt="" class="p-appicon" data-testid="app-icon" />
</template>
