<script setup lang="ts">
import EnvironmentSummary from "../environment/EnvironmentSummary.vue";
import { usePreview } from "../composables";
import NormalFileCard from "./NormalFileCard.vue";
import NormalRunCard from "./NormalRunCard.vue";
import NormalSettingsCard from "./NormalSettingsCard.vue";

/**
 * Normal mode home (iteration 2): one file, the everyday settings, start, and its state/result
 * on one screen. Left: file + settings; right: run (start → progress → result). Below ~880 px
 * the two columns stack. Several files, queues and history live in Expert.
 */
const { t } = usePreview();
</script>

<template>
  <section class="p-home" aria-labelledby="normal-heading" data-testid="normal-home">
    <div class="p-home__head">
      <h1 id="normal-heading" class="u-heading">{{ t("section.transcribe") }}</h1>
      <EnvironmentSummary />
    </div>
    <div class="p-home__grid">
      <div class="p-home__col">
        <NormalFileCard />
        <NormalSettingsCard />
      </div>
      <NormalRunCard />
    </div>
  </section>
</template>
