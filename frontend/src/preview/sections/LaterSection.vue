<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";

/** Models / setup: designed later (S4/S5). Shows the planned content, no operable controls. */
const props = defineProps<{ section: "models" | "setup" }>();
const { t } = usePreview();
const stage = computed(() => (props.section === "models" ? "UI-3 S4" : "UI-3 S4 / S5"));
</script>

<template>
  <section class="u-page u-stack" :aria-labelledby="`${section}-heading`" :data-testid="`section-${section}`">
    <h1 :id="`${section}-heading`" class="u-heading">{{ t(`section.${section}`) }}</h1>
    <v-alert type="info" :title="t('later.title')" data-testid="later-notice">
      <p>{{ t(`later.${section}`) }}</p>
      <p class="text-caption">{{ t("later.stage", { stage }) }}</p>
    </v-alert>
  </section>
</template>
