<script setup lang="ts">
import { computed, ref } from "vue";
import type { LegacyEnvironment, LegacyOption } from "../../api/legacy";
import { useI18n } from "../../composables/core";
import type { MessageKey } from "../../i18n";
import EnvironmentNotices from "./EnvironmentNotices.vue";

/**
 * Read-only categories of one environment answer (Expert, or "show details" in Normal).
 * Candidates are listed, never offered as choices: wiring them to a job is S3/S4.
 */
const props = defineProps<{
  data: LegacyEnvironment;
  requestedProfile: string | null;
  defaultProfile: string | null;
  open?: boolean;
}>();
const { t } = useI18n();

const SECTIONS = ["profiles", "devices", "candidates", "models", "notices"] as const;
const expanded = ref<string[]>(props.open ? [...SECTIONS] : []);

const profiles = computed(() =>
  props.data.profiles.map((p) => ({
    name: p.name,
    // Presence only here; compatibility is its own column (never merged into "built").
    status: t(p.exists ? "env.status.built" : "env.status.missing"),
    compatible: t(`env.compat.${String(p.compatible)}` as MessageKey),
    reason: p.compatible === false ? t(`env.reason.${p.compatibilityReason ?? "other"}` as MessageKey) : null,
    roles: [
      p.name === props.data.activeProfile ? t("env.role.active") : null,
      p.name === props.requestedProfile ? t("env.role.requested") : null,
      p.name === props.defaultProfile ? t("env.role.default") : null,
    ].filter((r): r is string => r !== null),
  })),
);

const devices = computed(() => props.data.devices);
const probeText = (status: string) => t(`env.probe.${status}` as MessageKey);
const list = (values: readonly string[]) => values.join(", ") || t("env.none");

function candidate(o: LegacyOption): string {
  return t("env.candidate", { label: o.label, models: o.modelCount, devices: list(o.devices.map((d) => d.label)) });
}
const candidates = computed(() => [
  { id: "asr", title: t("env.asr"), options: props.data.asr },
  { id: "diarization", title: t("env.diarization"), options: props.data.diarization },
]);
const nativeBuilt = computed(() => {
  const built = devices.value?.native?.built;
  return built === true ? t("env.status.built") : built === false ? t("env.status.missing") : t("env.unknown");
});
</script>

<template>
  <v-expansion-panels v-model="expanded" multiple variant="accordion" data-testid="env-details">
    <v-expansion-panel value="profiles" :title="t('env.detail.profiles')" data-testid="env-detail-profiles">
      <v-expansion-panel-text>
        <ul class="u-env-list">
          <li v-for="p in profiles" :key="p.name" :data-testid="`env-profile-row-${p.name}`">
            <span class="font-weight-medium">{{ p.name }}</span>
            — {{ t("env.detail.exists") }}: {{ p.status }} / {{ t("env.detail.compatible") }}: {{ p.compatible }}
            <span v-if="p.reason" class="text-medium-emphasis"> — {{ p.reason }}</span>
            <v-chip v-for="r in p.roles" :key="r" size="x-small" label class="ms-1">{{ r }}</v-chip>
          </li>
        </ul>
      </v-expansion-panel-text>
    </v-expansion-panel>

    <v-expansion-panel value="devices" :title="t('env.detail.devices')" data-testid="env-detail-devices">
      <v-expansion-panel-text>
        <p v-if="!devices" class="text-body-2">{{ t("env.detail.noDevices") }}</p>
        <div v-else class="u-stack">
          <p class="text-body-2">
            {{ t("env.detail.cpu", { logical: devices.cpu.logicalCores ?? t("env.unknown"), physical: devices.cpu.physicalCores ?? t("env.unknown") }) }}
          </p>
          <ul v-if="devices.accelerators.length" class="u-env-list" data-testid="env-accelerators">
            <li v-for="(a, i) in devices.accelerators" :key="i">
              {{ t(`env.runtime.${a.runtime}` as MessageKey) }}: {{ a.name }} — {{ a.usable ? t("env.usable") : t("env.notUsable") }}
            </li>
          </ul>
          <p v-else class="text-body-2">{{ t("env.detail.noAccelerators") }}</p>
          <p v-if="devices.openvino" class="text-body-2" data-testid="env-openvino-devices">
            {{ t("env.detail.openvino", { devices: list(devices.openvino.devices) }) }}
          </p>
          <div v-if="devices.probes.length">
            <h4 class="text-caption">{{ t("env.detail.probes") }}</h4>
            <ul class="u-env-list" data-testid="env-probes">
              <li v-for="p in devices.probes" :key="p.name">{{ p.name }}: {{ probeText(p.status) }} {{ p.cached ? t("env.probe.cached") : "" }}</li>
            </ul>
          </div>
        </div>
      </v-expansion-panel-text>
    </v-expansion-panel>

    <v-expansion-panel value="candidates" :title="t('env.detail.candidates')" data-testid="env-detail-candidates">
      <v-expansion-panel-text class="u-stack">
        <p class="text-caption">{{ t("env.detail.candidatesNote") }}</p>
        <div v-for="group in candidates" :key="group.id">
          <h4 class="u-subheading">{{ group.title }}</h4>
          <ul class="u-env-list" :data-testid="`env-${group.id}`">
            <li v-for="o in group.options" :key="o.id">{{ candidate(o) }}</li>
            <li v-if="group.options.length === 0">{{ t("env.none") }}</li>
          </ul>
        </div>
      </v-expansion-panel-text>
    </v-expansion-panel>

    <v-expansion-panel value="models" :title="`${t('env.detail.installed')} / ${t('env.detail.native')}`" data-testid="env-detail-models">
      <v-expansion-panel-text class="u-stack">
        <div>
          <h4 class="u-subheading">{{ t("env.detail.installed") }}</h4>
          <p v-if="data.installedModels === null" class="text-body-2">{{ t("env.notObtained") }}</p>
          <ul v-else class="u-env-list" data-testid="env-installed-models">
            <li v-for="(m, i) in data.installedModels" :key="i">{{ m.label }} <span class="text-medium-emphasis">({{ m.backend }})</span></li>
            <li v-if="data.installedModels.length === 0">{{ t("env.none") }}</li>
          </ul>
          <p v-if="data.openvinoIr" class="text-body-2" data-testid="env-openvino-ir">{{ t("env.detail.openvinoIr", data.openvinoIr) }}</p>
        </div>
        <div data-testid="env-native">
          <h4 class="u-subheading">{{ t("env.detail.native") }}</h4>
          <template v-if="devices?.native">
            <p class="text-body-2">{{ t("env.native.built", { state: nativeBuilt }) }}</p>
            <p class="text-body-2">{{ t("env.native.variants", { list: list(devices.native.variants) }) }}</p>
          </template>
          <p v-else class="text-body-2">{{ devices ? t("env.unknown") : t("env.notObtained") }}</p>
          <p v-if="data.nativeRunnable" class="text-body-2">{{ t("env.native.runnable", { list: list(data.nativeRunnable) }) }}</p>
          <p class="text-caption">{{ t("env.native.note") }}</p>
        </div>
      </v-expansion-panel-text>
    </v-expansion-panel>

    <v-expansion-panel value="notices" :title="t('env.detail.notices')" data-testid="env-detail-notices">
      <v-expansion-panel-text><EnvironmentNotices :data="data" /></v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>
