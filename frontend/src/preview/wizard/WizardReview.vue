<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { planFor } from "../domain/setup";
import type { PreviewKey } from "../i18n";

/**
 * "What will be installed" (B8: the read-only plan). Components with example sizes, what will run,
 * and what was agreed — in one short list. Nothing happens before "Approve and start".
 */
const { setupState, t } = usePreview();
const plan = computed(() => planFor(setupState.value.entry, setupState.value.choices));
const size = (mb: number) => (mb >= 1000 ? t("size.gb", { n: (mb / 1000).toFixed(1) }) : t("size.mb", { n: mb }));
const itemName = (id: string) => {
  if (id === "runtime") return t(setupState.value.choices.device === "gpu" ? "plan.item.runtimeGpu" : "plan.item.runtimeCpu");
  if (id === "asrModel") return t(setupState.value.choices.device === "gpu" ? "plan.item.asrModelGpu" : "plan.item.asrModelCpu");
  return t(`plan.item.${id}` as PreviewKey);
};
</script>

<template>
  <div class="u-stack p-wizard__page" data-testid="wizard-review">
    <h2 class="p-wizard__title">{{ t(setupState.entry === "repair" ? "wizard.review.repairTitle" : "wizard.step.review") }}</h2>
    <v-table density="compact" class="p-plan" data-testid="wizard-plan">
      <thead>
        <tr>
          <th>{{ t("plan.col.item") }}</th>
          <th class="text-end">{{ t("plan.col.download") }}</th>
          <th class="text-end">{{ t("plan.col.disk") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in plan.items" :key="item.id" :data-testid="`plan-item-${item.id}`">
          <td>{{ itemName(item.id) }}</td>
          <td class="text-end">{{ size(item.downloadMb) }}</td>
          <td class="text-end">{{ size(item.diskMb) }}</td>
        </tr>
        <tr class="font-weight-medium">
          <td>{{ t("plan.total") }}</td>
          <td class="text-end" data-testid="plan-download">{{ t("plan.approx", { size: size(plan.downloadMb) }) }}</td>
          <td class="text-end">{{ t("plan.approx", { size: size(plan.diskMb) }) }}</td>
        </tr>
      </tbody>
    </v-table>
    <dl class="p-dl text-body-medium">
      <dt>{{ t("plan.steps") }}</dt>
      <dd>{{ plan.stages.map((s) => t(`stage.${s}` as PreviewKey)).join(t("list.separator")) }}</dd>
      <template v-if="plan.needsAgreement">
        <dt>{{ t("plan.agreements") }}</dt>
        <dd>{{ t("plan.agreementDone") }}</dd>
      </template>
    </dl>
    <p class="text-body-medium" data-testid="wizard-no-side-effects">{{ t("wizard.review.nothingYet") }}</p>
  </div>
</template>
