<script setup lang="ts">
import { mdiAlertOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { isActive } from "../domain/demo";
import type { DevicePlan } from "../domain/setup";
import type { PreviewKey } from "../i18n";
import { useSetupNav } from "../wizard/useSetupNav";

/**
 * Tools → "Repair and reset…" (iteration 5, owner memo #32; P2 mock). Three different operations,
 * never mixed:
 *  1. repair one runtime (rebuild it; settings, history and models are kept) → the repair wizard;
 *  2. set one runtime up again (choose its models / features again; models are kept) → the wizard;
 *  3. reset the app (the user picks what is removed). This is a CONFIRMATION MOCK only: the final
 *     button stays disabled and nothing is removed or pretended to be removed. The product needs a
 *     decided retention / deletion policy first (iteration-5.md §9).
 */
const { layout, state, t } = usePreview();
const { openSetup } = useSetupNav();
const profiles = computed(() => (["gpu", "cpu"] as DevicePlan[]).filter((p) => state.value.env[p] !== "absent"));
const target = ref<DevicePlan | null>(null);
const chosen = computed(() => target.value ?? profiles.value[0] ?? null);
const RESET_ITEMS = ["settings", "presets", "history", "token", "models", "runtimes"] as const;
const picked = ref<string[]>([]);
const running = computed(() => state.value.jobs.filter(isActive).length);
function go(entry: "repair" | "add") {
  if (!chosen.value) return;
  layout.openDialog(null);
  openSetup(entry, { device: chosen.value });
}
</script>

<template>
  <div class="u-stack" data-testid="maintenance">
    <v-radio-group v-if="profiles.length > 0" v-model="target" inline hide-details :label="t('maint.target')" data-testid="maint-target">
      <v-radio v-for="p in profiles" :key="p" :value="p" color="primary" :label="t(`env.profile.${p}`)" :data-testid="`maint-target-${p}`" />
    </v-radio-group>

    <section class="u-stack" aria-labelledby="maint-repair" data-testid="maint-repair">
      <h3 id="maint-repair" class="text-body-large font-weight-medium">{{ t("maint.repair.title") }}</h3>
      <p class="text-body-medium">{{ t("maint.repair.body") }}</p>
      <v-btn variant="tonal" color="primary" class="align-self-start" :disabled="!chosen" data-testid="maint-repair-open" @click="go('repair')">{{ t("maint.repair.button") }}</v-btn>
    </section>
    <v-divider />
    <section class="u-stack" aria-labelledby="maint-resetup" data-testid="maint-resetup">
      <h3 id="maint-resetup" class="text-body-large font-weight-medium">{{ t("maint.resetup.title") }}</h3>
      <p class="text-body-medium">{{ t("maint.resetup.body") }}</p>
      <v-btn variant="tonal" color="primary" class="align-self-start" :disabled="!chosen" data-testid="maint-resetup-open" @click="go('add')">{{ t("maint.resetup.button") }}</v-btn>
    </section>
    <v-divider />
    <section class="u-stack" aria-labelledby="maint-reset" data-testid="maint-reset">
      <h3 id="maint-reset" class="text-body-large font-weight-medium">{{ t("maint.reset.title") }}</h3>
      <p class="text-body-medium">{{ t("maint.reset.body") }}</p>
      <fieldset class="p-fieldset">
        <legend class="text-body-medium font-weight-medium">{{ t("maint.reset.pick") }}</legend>
        <v-checkbox v-for="item in RESET_ITEMS" :key="item" v-model="picked" :value="item" color="primary" hide-details density="compact" :data-testid="`maint-reset-${item}`">
          <template #label>
            <div>
              <div>{{ t(`maint.reset.item.${item}` as PreviewKey) }}</div>
              <div class="text-body-medium">{{ t(`maint.reset.effect.${item}` as PreviewKey) }}</div>
            </div>
          </template>
        </v-checkbox>
      </fieldset>
      <p class="text-body-medium">{{ t("maint.reset.keeps") }}</p>
      <p v-if="running > 0" class="p-reason" role="status" data-testid="maint-reset-running">
        <v-icon :icon="mdiAlertOutline" size="small" color="warning" aria-hidden="true" />
        <span>{{ t("maint.reset.running", { count: running }) }}</span>
      </p>
      <p class="text-body-medium">{{ t("maint.reset.backup") }}</p>
      <div class="u-row">
        <v-btn color="error" variant="tonal" disabled data-testid="maint-reset-confirm">{{ t("maint.reset.confirm", { count: picked.length }) }}</v-btn>
      </div>
      <p class="text-body-medium" data-testid="maint-reset-later">{{ t("maint.reset.later") }}</p>
    </section>
  </div>
</template>
