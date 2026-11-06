<script setup lang="ts">
import { mdiPencilOutline } from "@mdi/js";
import { computed, ref } from "vue";
import { usePreview } from "../composables";
import { lockedModels, primaryChoosable, unavailableModels } from "../domain/setup";
import { MAX_MODEL_REFS, modelsFor, RECOMMENDED_MODEL, SETUP_ENGINES, SETUP_MODELS, type SetupEngine, setupModel } from "../domain/setupCatalog";
import type { PreviewKey } from "../i18n";
import { useSetupText } from "./useSetupText";

/**
 * Model choice (iteration 5, #30).
 *  - Standard: the recommended model for the device is SHOWN, never silently fixed; "Change model…"
 *    opens a short list of the models offered for this device.
 *  - Custom: engine, several models and the main one. Engines the prototype does not set up and
 *    models the device is not offered for are listed but cannot be selected.
 * Installed models of the target profile are kept (checked, cannot be removed here).
 * All candidates come from the catalog fixture (`domain/setupCatalog.ts`).
 */
const { setup, setupState, t } = usePreview();
const { size, note, modelName } = useSetupText();
const changing = ref(false);
const c = computed(() => setupState.value.choices);
const locked = computed(() => lockedModels(setupState.value));
const offered = computed(() => modelsFor(c.value.engine, c.value.device));
const engineModels = computed(() => SETUP_MODELS.filter((m) => m.engine === c.value.engine));
const unavailable = computed(() => unavailableModels(c.value));
const recommended = computed(() => RECOMMENDED_MODEL[c.value.device]);
const engines = computed(() => SETUP_ENGINES.map((e) => ({ value: e.id, title: t(`opt.backend.${e.id}` as PreviewKey), props: { disabled: !e.selectable, subtitle: e.selectable ? undefined : t("wizard.models.engineLater") } })));
const primaryItems = computed(() => c.value.models.map((m) => ({ value: m, title: modelName(m) })));
const deviceName = computed(() => t(c.value.device === "gpu" ? "wizard.env.gpuShort" : "wizard.env.cpuShort"));
function pick(id: string) {
  setup.chooseModel(id);
  changing.value = false;
}
</script>

<template>
  <section class="u-stack" aria-labelledby="wizard-models-heading" data-testid="wizard-models" :data-method="c.method">
    <h3 id="wizard-models-heading" class="text-body-large font-weight-medium">{{ t("wizard.models.title") }}</h3>

    <template v-if="c.method === 'standard'">
      <ul class="p-modelsum" data-testid="wizard-model-summary">
        <li v-for="m in c.models" :key="m" :data-testid="`wizard-model-chosen-${m}`">
          <span class="font-weight-medium">{{ modelName(m) }}</span>
          <v-chip v-if="m === recommended" label size="small" color="primary" variant="tonal">{{ t("env.recommended") }}</v-chip>
          <v-chip v-if="locked.includes(m)" label size="small" variant="outlined">{{ t("wizard.models.installed") }}</v-chip>
          <span class="text-body-medium">{{ size(setupModel(m)?.sizeMb ?? null) }}</span>
        </li>
      </ul>
      <div>
        <v-btn variant="outlined" :prepend-icon="mdiPencilOutline" :aria-expanded="changing" data-testid="wizard-change-model" @click="changing = !changing">{{ t("wizard.models.change") }}</v-btn>
      </div>
      <v-radio-group v-if="changing" :model-value="c.primaryModel" :label="t('wizard.models.choose', { device: deviceName })" hide-details data-testid="wizard-model-list" @update:model-value="(v) => v && pick(String(v))">
        <v-radio v-for="m in offered" :key="m.id" :value="m.id" color="primary" :data-testid="`wizard-model-option-${m.id}`">
          <template #label>
            <div>
              <div class="font-weight-medium">{{ m.displayName }}<span v-if="m.id === recommended"> — {{ t("env.recommended") }}</span></div>
              <div class="text-body-medium">{{ note(m.id) }} · {{ size(m.sizeMb) }}</div>
            </div>
          </template>
        </v-radio>
      </v-radio-group>
    </template>

    <template v-else>
      <v-select
        :label="t('wizard.models.engine')"
        :items="engines"
        :model-value="c.engine"
        data-testid="wizard-engine"
        @update:model-value="(v) => setup.setEngine(v as SetupEngine)"
      />
      <fieldset class="p-fieldset" data-testid="wizard-model-checks">
        <legend class="text-body-medium font-weight-medium">{{ t("wizard.models.pick", { max: MAX_MODEL_REFS }) }}</legend>
        <v-checkbox
          v-for="m in engineModels"
          :key="m.id"
          :model-value="c.models.includes(m.id)"
          :disabled="locked.includes(m.id) || (!m.devices.includes(c.device) && !c.models.includes(m.id))"
          color="primary"
          hide-details
          density="compact"
          :data-testid="`wizard-model-check-${m.id}`"
          @update:model-value="(v) => setup.toggleModel(m.id, Boolean(v))"
        >
          <template #label>
            <div>
              <span class="font-weight-medium">{{ m.displayName }}</span>
              <span class="text-body-medium"> · {{ size(m.sizeMb) }}</span>
              <span v-if="locked.includes(m.id)" class="text-body-medium"> · {{ t("wizard.models.installed") }}</span>
              <div class="text-body-medium">{{ m.devices.includes(c.device) ? note(m.id) : t("wizard.models.notForDevice", { device: deviceName }) }}</div>
            </div>
          </template>
        </v-checkbox>
      </fieldset>
      <v-select
        v-if="primaryChoosable(setupState) && c.models.length > 1"
        :label="t('wizard.models.primary')"
        :items="primaryItems"
        :model-value="c.primaryModel"
        data-testid="wizard-primary-model"
        @update:model-value="(v) => setup.setPrimaryModel(String(v))"
      />
    </template>

    <p v-if="!primaryChoosable(setupState)" class="text-body-medium" data-testid="wizard-default-kept">{{ t("wizard.models.defaultKept") }}</p>
    <div v-if="unavailable.length > 0" class="p-reason" role="status" data-testid="wizard-models-unavailable">
      <span>{{ t("wizard.models.unavailable", { names: unavailable.map(modelName).join(t("list.separator")), device: deviceName }) }}</span>
      <v-btn variant="text" color="primary" data-testid="wizard-remove-unavailable" @click="setup.removeUnavailableModels()">{{ t("wizard.models.removeUnavailable") }}</v-btn>
    </div>
  </section>
</template>
