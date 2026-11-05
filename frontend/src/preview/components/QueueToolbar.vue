<script setup lang="ts">
import { mdiPause, mdiPlay, mdiStepForward, mdiBroom } from "@mdi/js";
import { computed } from "vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";

/** Queue-wide controls: pause/resume (product), clear finished (product), step all (demo only). */
const { demo, state, t } = usePreview();
const counts = computed(() => ({
  active: state.value.jobs.filter(isActive).length,
  finished: state.value.jobs.filter((j) => !isActive(j)).length,
}));
</script>

<template>
  <div class="u-row" data-testid="queue-toolbar">
    <v-chip size="small" label :color="state.queuePaused ? 'warning' : undefined" variant="tonal" data-testid="queue-paused">
      {{ state.queuePaused ? t("queue.paused") : t("queue.running") }}
    </v-chip>
    <v-btn size="small" variant="tonal" :prepend-icon="state.queuePaused ? mdiPlay : mdiPause" data-testid="queue-pause" @click="demo.togglePause()">
      {{ state.queuePaused ? t("queue.resume") : t("queue.pause") }}
    </v-btn>
    <v-btn size="small" variant="text" :prepend-icon="mdiBroom" :disabled="counts.finished === 0" data-testid="queue-clear" @click="demo.clearFinished()">
      {{ t("queue.clearFinished") }}
    </v-btn>
    <v-btn size="small" variant="outlined" :prepend-icon="mdiStepForward" :disabled="counts.active === 0" data-testid="queue-step-all" @click="demo.stepAll()">
      {{ t("demo.stepAll") }}
    </v-btn>
  </div>
</template>
