<script setup lang="ts">
import { computed } from "vue";
import { usePreview } from "../composables";
import { credentialPresent, DIARIZATION_TERMS, lockedModels, missingRequirements, planFor, type PlanItem } from "../domain/setup";
import type { PreviewKey } from "../i18n";
import { useSetupText } from "./useSetupText";

/**
 * "Review installation" (B8: the read-only plan), iteration 5 (#27): one table of what will be
 * installed with example sizes, what is already there and stays, and — only with diarization — the
 * token and terms state. No step-by-step prose. Nothing happens before "Start installation".
 */
const { setup, setupState, t } = usePreview();
const { size, modelName } = useSetupText();
const plan = computed(() => planFor(setupState.value.entry, setupState.value.choices, setupState.value.inventory));
// What is actually given for THIS plan (never "accepted" just because the plan needs it).
const missing = computed(() => missingRequirements(setupState.value));
const termsAccepted = computed(() => setupState.value.choices.acceptedAgreements.includes(DIARIZATION_TERMS));
const device = computed(() => setupState.value.choices.device);
const runtimeKept = computed(() => setupState.value.entry !== "repair" && setupState.value.inventory.ready[device.value]);
const keptModels = computed(() => lockedModels(setupState.value));
const total = (mb: number) => (plan.value.sizeUnknown ? t("plan.approxPartly", { size: size(mb) }) : t("plan.approx", { size: size(mb) }));
const itemName = (item: PlanItem) => {
  if (item.id === "runtime") return t(device.value === "gpu" ? "plan.item.runtimeGpu" : "plan.item.runtimeCpu");
  if (item.id === "asrModel") return t("plan.item.asrModel", { name: modelName(item.modelId ?? "") });
  if (item.id === "repairRuntime") return t(device.value === "gpu" ? "plan.item.repairRuntimeGpu" : "plan.item.repairRuntimeCpu");
  return t(`plan.item.${item.id}` as PreviewKey);
};
const testid = (item: PlanItem) => (item.id === "asrModel" ? `plan-item-model-${item.modelId}` : `plan-item-${item.id}`);
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
        <tr v-for="item in plan.items" :key="testid(item)" :data-testid="testid(item)">
          <td>{{ itemName(item) }}</td>
          <td class="text-end">{{ size(item.downloadMb) }}</td>
          <td class="text-end">{{ size(item.diskMb) }}</td>
        </tr>
        <tr v-if="plan.items.length === 0"><td colspan="3" class="text-body-medium" data-testid="plan-empty">{{ t("plan.empty") }}</td></tr>
        <tr v-if="runtimeKept" class="p-plan__kept" data-testid="plan-kept-runtime">
          <td>{{ t(device === "gpu" ? "plan.item.runtimeGpu" : "plan.item.runtimeCpu") }} — {{ t("plan.kept") }}</td>
          <td class="text-end">—</td>
          <td class="text-end">—</td>
        </tr>
        <tr v-for="m in keptModels" :key="`kept-${m}`" class="p-plan__kept" :data-testid="`plan-kept-model-${m}`">
          <td>{{ t("plan.item.asrModel", { name: modelName(m) }) }} — {{ t("plan.kept") }}</td>
          <td class="text-end">—</td>
          <td class="text-end">—</td>
        </tr>
        <tr class="font-weight-medium">
          <td>{{ t("plan.total") }}</td>
          <td class="text-end" data-testid="plan-download">{{ total(plan.downloadMb) }}</td>
          <td class="text-end" data-testid="plan-disk">{{ total(plan.diskMb) }}</td>
        </tr>
      </tbody>
    </v-table>
    <dl v-if="plan.requiresCredential || plan.requiredAgreements.length > 0" class="p-dl text-body-medium" data-testid="plan-requirements">
      <template v-if="plan.requiresCredential">
        <dt>{{ t("plan.credential") }}</dt>
        <dd data-testid="plan-credential" :data-ok="credentialPresent(setupState)">{{ credentialPresent(setupState) ? t("plan.credentialPresent") : t("plan.credentialMissing") }}</dd>
      </template>
      <template v-if="plan.requiredAgreements.length > 0">
        <dt>{{ t("plan.agreements") }}</dt>
        <dd data-testid="plan-agreement" :data-ok="termsAccepted">{{ termsAccepted ? t("plan.agreementDone") : t("plan.agreementMissing") }}</dd>
      </template>
    </dl>
    <v-btn v-if="missing.length > 0" variant="outlined" class="align-self-start" data-testid="wizard-fix-requirements" @click="setup.goTo('features')">{{ t("wizard.review.fixRequirements") }}</v-btn>
  </div>
</template>
