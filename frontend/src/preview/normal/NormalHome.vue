<script setup lang="ts">
import { usePreview } from "../composables";
import NormalFileCard from "./NormalFileCard.vue";
import NormalRunCard from "./NormalRunCard.vue";
import NormalSettingsCard from "./NormalSettingsCard.vue";
import SetupNotice from "../wizard/SetupNotice.vue";

/**
 * Normal mode home: choose a file → the few settings → start → result, on one screen.
 * Left: file + settings; right: run and result. Below ~880 px the two columns stack.
 * Several files, the queue and history live in Expert and are not mentioned here.
 */
const { t } = usePreview();
</script>

<template>
  <section class="p-home" aria-labelledby="normal-heading" data-testid="normal-home">
    <h1 id="normal-heading" class="p-sr-only">{{ t("section.transcribe") }}</h1>
    <SetupNotice class="mb-4" />
    <div class="p-home__grid">
      <div class="p-home__col">
        <NormalFileCard />
        <NormalSettingsCard />
      </div>
      <NormalRunCard />
    </div>
  </section>
</template>
