<script setup lang="ts">
import { computed, onMounted } from "vue";
import type { ProfileKind, StepProgressView } from "../../api/setup";
import ErrorAlert from "../../components/ErrorAlert.vue";
import { useCore, useI18n } from "../../composables/core";
import { useStore } from "../../composables/useStore";
import { ASR_MODELS, isTerminal, nativeProfile, NATIVE_MODELS, readinessKey, VERIFIABLE_PROFILES, verifyRefusal, WORKER_MODELS, type NativeVariant, type DiarizationDevice } from "../../domain/v1/setup";
import type { MessageKey } from "../../i18n";
import { ja } from "../../i18n/ja";

/**
 * 束F: the new GUI setup (`--next-ui-v1` only). Diagnostics → standard/custom choice → plan
 * (read-only) → review → explicit start → progress (cancel/retry/resume) → result and readiness.
 * Nothing is downloaded, built or verified before the person starts the reviewed plan.
 */
const core = useCore();
if (!core.v1) throw new Error("v1 core is not active");
const setup = core.v1.setup;
const library = useStore(core.v1.library.store);
const token = useStore(core.token.store);
const state = useStore(setup.store);
const { t } = useI18n();

const known = (key: string): MessageKey | null => (key in ja ? (key as MessageKey) : null);
const text = (prefix: string, code: string | null | undefined, fallback: MessageKey = "fs.code.other") =>
  code ? t(known(`${prefix}.${code}`) ?? fallback, { code }) : "";

const diag = computed(() => state.value.diagnostics.data);
const detecting = computed(() => state.value.diagnostics.phase === "pending" || diag.value?.phase === "running");
const op = computed(() => state.value.operation);
const running = computed(() => op.value !== null && !isTerminal(op.value));
const plan = computed(() => state.value.plan.plan);
const choice = computed(() => state.value.choice);
const custom = computed(() => state.value.mode === "custom");
const tokenPresent = computed(() => token.value.status?.configured === true);
const selectedPreset = computed(() => library.value.preset.item);
const verifyBlocked = computed(() => verifyRefusal(selectedPreset.value?.settings ?? null));

const profiles = computed(() =>
  (["cpu", "cuda", "intel", "vulkan"] as ProfileKind[]).map((value) => ({
    value,
    title: t(`fs.profile.${value}` as MessageKey),
    props: { disabled: !VERIFIABLE_PROFILES.includes(value), subtitle: VERIFIABLE_PROFILES.includes(value) ? undefined : t("fs.profile.unsupported") },
  })),
);
const modelChoices = computed<readonly string[]>(() => nativeProfile(choice.value.profile) ? NATIVE_MODELS : ASR_MODELS);
const workerModels = computed<readonly string[]>(() => nativeProfile(choice.value.profile) ? NATIVE_MODELS : WORKER_MODELS);
const primaryChoices = computed(() => choice.value.models.filter((m) => workerModels.value.includes(m)));
const variants: NativeVariant[] = ["vulkan", "openvino_vulkan", "openvino"];
const diarizationDevices = computed<DiarizationDevice[]>(() => {
  if (choice.value.profile === "intel") return ["auto", "xpu", "cpu"];
  if (choice.value.profile === "cuda") return ["auto", "cuda", "cpu"];
  return ["auto", "cpu"];
});

function toggleModel(model: string, on: boolean | null): void {
  const models = on ? [...new Set([...choice.value.models, model])] : choice.value.models.filter((m) => m !== model);
  if (models.length === 0) return;
  const primary = models.includes(choice.value.primary) ? choice.value.primary : (models.find((m) => workerModels.value.includes(m)) ?? choice.value.primary);
  setup.edit({ models, primary });
}

function bytes(value: number | null): string {
  if (value === null) return t("fs.unknown");
  const gib = value / 1024 ** 3;
  return gib >= 1 ? `${gib.toFixed(1)} GiB` : `${Math.max(1, Math.round(value / 1024 ** 2))} MiB`;
}
const tri = (value: boolean | null) => (value === true ? t("fs.tri.yes") : value === false ? t("fs.tri.no") : t("fs.tri.unknown"));
const stepText = (step: StepProgressView) => `${t((known(`fs.step.${step.kind}`) ?? "fs.code.other") as MessageKey, { code: step.kind })}`;
const canStart = computed(
  () =>
    plan.value !== null &&
    plan.value.missingCredentials.length === 0 &&
    (plan.value.requiredAgreements.length === 0 || choice.value.agreementAccepted) &&
    state.value.start.phase !== "pending",
);
// Shown only for the Preset/Policy revisions it was computed for (a later selection hides it).
const readiness = computed(() =>
  state.value.readiness.key !== null && state.value.readiness.key === readinessKey(library.value.preset.item, library.value.policy.item)
    ? state.value.readiness.data
    : null,
);
const verifiable = computed(() => readiness.value?.candidates.find((c) => c.verifiable) ?? null);

onMounted(() => {
  void setup.loadDiagnostics();
  void setup.recover();
  if (token.value.status === null) void core.token.load();
});
</script>

<template>
  <v-card :title="t('fs.title')" class="u-card" data-testid="fsetup">
    <v-card-text class="u-stack">
      <p class="text-body-2">{{ t("fs.body") }}</p>

      <!-- 1. diagnostics -->
      <section class="u-stack" aria-labelledby="fsetup-diag" data-testid="fsetup-diagnostics">
        <h2 id="fsetup-diag" class="text-subtitle-1">{{ t("fs.diag.title") }}</h2>
        <div v-if="detecting" role="status" data-testid="fsetup-detecting">
          <v-progress-linear indeterminate color="primary" />{{ t("fs.diag.running") }}
        </div>
        <ErrorAlert
          v-if="state.diagnostics.phase === 'error' && state.diagnostics.error"
          :error="state.diagnostics.error"
          :title="t('fs.diag.failed')"
          testid="fsetup-diag-error"
          :on-retry="() => setup.loadDiagnostics()"
        />
        <template v-if="diag">
          <v-alert v-if="!diag.os.venvBuildSupported" type="info" density="compact" data-testid="fsetup-os-limited">{{ t("fs.diag.osLimited") }}</v-alert>
          <div data-testid="fsetup-gpu">
            <strong>{{ t("fs.diag.gpu") }}:</strong>
            <span v-if="!diag.gpu">{{ t("fs.unknown") }}</span>
            <span v-else-if="diag.gpu.state === 'unknown'" data-testid="fsetup-gpu-unknown">{{ text("fs.reason", diag.gpu.reasonCode) }}</span>
            <span v-else-if="diag.gpu.state === 'none'">{{ t("fs.diag.noGpu") }}</span>
            <v-chip v-for="(a, i) in diag.gpu?.adapters ?? []" :key="i" size="small" class="ma-1" :data-testid="`fsetup-adapter-${i}`">
              {{ a.name }}<template v-if="a.driverVersion"> ({{ t("fs.diag.driver", { version: a.driverVersion }) }})</template>
            </v-chip>
          </div>
          <v-alert
            v-if="diag.recommendation?.reasonCodes.includes('nvidia_driver_unknown')"
            type="warning"
            density="compact"
            data-testid="fsetup-driver-missing"
          >
            {{ t("fs.diag.driverMissing") }} <code>{{ diag.gpu?.driverDownloadUrl }}</code>
          </v-alert>
          <div data-testid="fsetup-vcredist">
            <strong>{{ t("fs.diag.vcredist") }}:</strong> {{ text("fs.vc", diag.vcredist?.state ?? "unknown") }}
            <span v-if="diag.vcredist?.version"> ({{ diag.vcredist.version }})</span>
          </div>
          <v-alert
            v-if="diag.vcredist && ['missing', 'partial'].includes(diag.vcredist.state)"
            type="warning"
            density="compact"
            data-testid="fsetup-vcredist-missing"
          >
            {{ t("fs.vc.fix") }} <code>{{ diag.vcredist.downloadUrl }}</code>
          </v-alert>
          <div data-testid="fsetup-runtime">
            <strong>{{ t("fs.diag.runtime") }}:</strong>
            <template v-if="diag.runtimePhase === 'running'">{{ t("fs.diag.runtimeRunning") }}</template>
            <template v-else-if="!diag.runtime">{{ t("fs.diag.runtimeNotRun") }}</template>
            <template v-else-if="diag.runtime.reasonCode && diag.runtime.ctranslate2Cuda === null">
              <span data-testid="fsetup-runtime-reason">{{ text("fs.reason", diag.runtime.reasonCode) }}</span>
            </template>
            <template v-else-if="diag.runtime.profile === 'intel'">
              {{ t("fs.diag.vulkan") }} {{ tri(diag.runtime.vulkan ?? null) }} ・
              {{ t("fs.diag.xpu") }} {{ tri(diag.runtime.torchXpu ?? null) }} ・
              {{ t("fs.diag.openvino") }} {{ tri(diag.runtime.openvinoGpu ?? null) }}
            </template>
            <template v-else>
              {{ t("fs.diag.ct2", { value: tri(diag.runtime.ctranslate2Cuda) }) }} ・ {{ t("fs.diag.torch", { value: tri(diag.runtime.torchCuda) }) }}
              <span v-if="diag.runtime.attempts > 1" data-testid="fsetup-runtime-retried"> ・ {{ t("fs.diag.retried") }}</span>
            </template>
          </div>
          <div v-if="diag.recommendation" data-testid="fsetup-recommendation">
            <strong>{{ t("fs.diag.recommended", { profile: t(`fs.profile.${diag.recommendation.profile}` as MessageKey) }) }}</strong>
            <ul class="text-body-2">
              <li v-for="code in diag.recommendation.reasonCodes" :key="code">{{ text("fs.reason", code) }}</li>
            </ul>
          </div>
        </template>
        <div class="u-row">
          <v-btn
            v-if="!diag || diag.phase === 'idle'"
            color="primary"
            :loading="detecting"
            :disabled="detecting || running"
            data-testid="fsetup-detect"
            @click="setup.redetect(false)"
          >
            {{ t("fs.diag.detect") }}
          </v-btn>
          <v-btn v-else variant="outlined" :loading="detecting" :disabled="detecting || running" data-testid="fsetup-redetect" @click="setup.redetect(true)">
            {{ t("fs.diag.redetect") }}
          </v-btn>
        </div>
      </section>

      <!-- 2. choice -->
      <section v-if="!running && state.mode !== 'verify'" class="u-stack" aria-labelledby="fsetup-choice" data-testid="fsetup-choice">
        <h2 id="fsetup-choice" class="text-subtitle-1">{{ t("fs.choice.title") }}</h2>
        <v-btn-toggle :model-value="state.mode" mandatory density="comfortable" @update:model-value="(m: 'standard' | 'custom') => setup.setMode(m)">
          <v-btn value="standard" data-testid="fsetup-mode-standard">{{ t("fs.mode.standard") }}</v-btn>
          <v-btn value="custom" data-testid="fsetup-mode-custom">{{ t("fs.mode.custom") }}</v-btn>
        </v-btn-toggle>
        <p v-if="!custom" data-testid="fsetup-standard-summary">
          {{ t("fs.choice.standard", { profile: t(`fs.profile.${choice.profile}` as MessageKey), model: choice.primary }) }}
        </p>
        <template v-else>
          <v-select
            :label="t('fs.choice.profile')"
            :items="profiles"
            :model-value="choice.profile"
            class="u-w-md"
            data-testid="fsetup-profile"
            @update:model-value="(p: ProfileKind) => setup.edit({ profile: p })"
          />
          <v-select
            v-if="choice.profile === 'intel'"
            :label="t('fs.choice.variant')"
            :items="variants"
            :model-value="choice.variant ?? 'vulkan'"
            data-testid="gsetup-variant"
            @update:model-value="(variant: NativeVariant) => setup.edit({ variant })"
          />
          <fieldset class="u-stack" data-testid="fsetup-models">
            <legend>{{ t("fs.choice.models") }}</legend>
            <v-checkbox
              v-for="m in modelChoices"
              :key="m"
              :label="workerModels.includes(m) ? m : `${m} — ${t('fs.choice.downloadOnly')}`"
              :model-value="choice.models.includes(m)"
              density="compact"
              hide-details
              :data-testid="`fsetup-model-${m}`"
              @update:model-value="(on: boolean | null) => toggleModel(m, on)"
            />
          </fieldset>
          <v-radio-group
            :label="t('fs.choice.primary')"
            :model-value="choice.primary"
            data-testid="fsetup-primary"
            @update:model-value="(p: string | null) => p && setup.edit({ primary: p })"
          >
            <v-radio v-for="m in primaryChoices" :key="m" :label="m" :value="m" :data-testid="`fsetup-primary-${m}`" />
          </v-radio-group>
        </template>
        <v-switch
          :label="t('fs.choice.diarization')"
          :model-value="choice.diarization"
          color="primary"
          hide-details
          data-testid="fsetup-diarization"
          @update:model-value="(on: boolean | null) => setup.edit({ diarization: on === true })"
        />
        <div v-if="choice.diarization" class="u-row">
          <v-select
            :label="t('fs.choice.diarizationDevice')"
            :items="diarizationDevices"
            :model-value="choice.diarizationDevice ?? 'auto'"
            data-testid="gsetup-diarization-device"
            @update:model-value="(diarizationDevice: DiarizationDevice) => setup.edit({ diarizationDevice })"
          />
          <v-text-field
            :label="t('fs.choice.speakers')"
            type="number" min="1" max="100"
            :model-value="choice.numSpeakers ?? ''"
            data-testid="gsetup-speaker-count"
            @update:model-value="(value: string) => setup.edit({ numSpeakers: value ? Number(value) : null })"
          />
        </div>
        <v-alert v-if="choice.diarization" type="info" density="compact" data-testid="fsetup-diarization-note">
          <p>{{ t("fs.choice.diarizationNote") }}</p>
          <p data-testid="fsetup-token-state">{{ tokenPresent ? t("fs.choice.tokenPresent") : t("fs.choice.tokenMissing") }}</p>
          <v-checkbox
            :label="t('fs.choice.agreement')"
            :model-value="choice.agreementAccepted"
            density="compact"
            hide-details
            data-testid="fsetup-agreement"
            @update:model-value="(on: boolean | null) => setup.edit({ agreementAccepted: on === true })"
          />
        </v-alert>
        <div class="u-row">
          <v-btn color="primary" :loading="state.plan.phase === 'pending'" :disabled="state.plan.phase === 'pending'" data-testid="fsetup-plan" @click="setup.createPlan()">
            {{ t("fs.plan.create") }}
          </v-btn>
        </div>
      </section>

      <ErrorAlert
        v-if="state.plan.phase === 'error' && state.plan.error"
        :error="state.plan.error"
        :title="t('fs.plan.failed')"
        testid="fsetup-plan-error"
        :on-retry="() => setup.createPlan()"
      />

      <!-- 3. review -->
      <section v-if="plan && !running && !op && state.mode !== 'verify'" class="u-stack" aria-labelledby="fsetup-review" data-testid="fsetup-review">
        <h2 id="fsetup-review" class="text-subtitle-1">{{ t("fs.review.title") }}</h2>
        <ul class="text-body-2" data-testid="fsetup-changes">
          <li v-for="(c, i) in plan.changes" :key="i">{{ t(`fs.change.${c.kind}.${c.action}` as MessageKey, { id: c.id }) }}</li>
        </ul>
        <p data-testid="fsetup-estimate">{{ t("fs.review.download", { size: bytes(plan.estimate.downloadBytes) }) }}</p>
        <v-alert v-if="plan.steps.some((step) => step.kind === 'native_build')" type="info" density="compact">
          {{ t("fs.review.nativeTools") }}
        </v-alert>
        <ol class="text-body-2" data-testid="fsetup-steps">
          <li v-for="s in plan.steps" :key="s.id">{{ t((known(`fs.step.${s.kind}`) ?? "fs.code.other") as MessageKey, { code: s.kind }) }}<template v-if="s.target"> — {{ s.target }}</template></li>
        </ol>
        <p v-if="plan.preserved.length" class="text-body-2" data-testid="fsetup-preserved">{{ t("fs.review.preserved", { list: plan.preserved.join(", ") }) }}</p>
        <v-alert v-if="plan.missingCredentials.length" type="warning" density="compact" data-testid="fsetup-credential-missing">{{ t("fs.review.credentialMissing") }}</v-alert>
        <v-alert v-if="plan.requiredAgreements.length && !choice.agreementAccepted" type="warning" density="compact" data-testid="fsetup-agreement-missing">
          {{ t("fs.review.agreementMissing") }}
        </v-alert>
        <p class="text-body-2">{{ t("fs.review.confirm") }}</p>
        <div class="u-row">
          <v-btn color="primary" :disabled="!canStart" :loading="state.start.phase === 'pending'" data-testid="fsetup-start" @click="setup.confirmAndStart()">
            {{ t("fs.review.start") }}
          </v-btn>
          <v-btn variant="text" :disabled="state.start.phase === 'pending'" data-testid="fsetup-discard" @click="setup.discardPlan()">{{ t("fs.review.discard") }}</v-btn>
        </div>
        <ErrorAlert v-if="state.start.phase === 'error' && state.start.error" :error="state.start.error" :title="t('fs.review.startFailed')" testid="fsetup-start-error" />
      </section>

      <!-- 4. operation -->
      <section v-if="op" class="u-stack" aria-labelledby="fsetup-op" data-testid="fsetup-operation" :data-status="op.status">
        <h2 id="fsetup-op" class="text-subtitle-1">{{ t("fs.op.title") }}</h2>
        <p role="status" data-testid="fsetup-op-status">{{ text("fs.status", op.status) }}<template v-if="op.waitingReason"> — {{ text("fs.wait", op.waitingReason) }}</template></p>
        <v-progress-linear v-if="running" indeterminate color="primary" />
        <v-alert v-if="op.status === 'queued' && state.queue.dispatch === 'paused'" type="info" density="compact" data-testid="fsetup-queue-paused">
          <p>{{ t("fs.op.queuePaused") }}</p>
          <v-btn class="mt-1" color="primary" variant="outlined" :loading="state.queue.phase === 'pending'" data-testid="fsetup-queue-resume" @click="setup.resumeDispatch()">
            {{ t("fs.op.queueResume") }}
          </v-btn>
        </v-alert>
        <p v-else-if="op.status === 'queued' && state.queue.ahead > 0" class="text-body-2" data-testid="fsetup-queue-ahead">{{ t("fs.op.queueAhead", { n: state.queue.ahead }) }}</p>
        <ErrorAlert v-if="state.queue.phase === 'error' && state.queue.error" :error="state.queue.error" :title="t('fs.op.queueFailed')" testid="fsetup-queue-error" />
        <ul class="text-body-2" data-testid="fsetup-op-steps">
          <li v-for="s in op.steps" :key="s.stepId" :data-step="s.kind" :data-status="s.status">
            {{ stepText(s) }}: {{ text("fs.stepStatus", s.status) }}<template v-if="s.reasonCode"> ({{ text("fs.stepReason", s.reasonCode) }})</template>
          </li>
        </ul>
        <v-alert v-if="op.error" type="error" density="compact" data-testid="fsetup-op-error">{{ text("fs.opError", op.error.code) }}</v-alert>
        <v-alert v-if="op.recoveryRequired" type="warning" density="compact" data-testid="fsetup-op-recovery">{{ t("fs.op.recovery") }}</v-alert>
        <div class="u-row">
          <v-btn v-if="['queued', 'running', 'waiting_user', 'interrupted'].includes(op.status)" variant="outlined" :loading="state.action.phase === 'pending'" data-testid="fsetup-cancel" @click="setup.act('cancel')">
            {{ t("fs.op.cancel") }}
          </v-btn>
          <v-btn v-if="['waiting_user', 'interrupted'].includes(op.status)" color="primary" :loading="state.action.phase === 'pending'" data-testid="fsetup-resume" @click="setup.act('resume')">
            {{ t("fs.op.resume") }}
          </v-btn>
          <v-btn v-if="['failed', 'cancelled'].includes(op.status)" color="primary" :loading="state.action.phase === 'pending'" data-testid="fsetup-retry" @click="setup.act('retry')">
            {{ t("fs.op.retry") }}
          </v-btn>
          <v-btn v-if="!running" variant="text" data-testid="fsetup-dismiss" @click="setup.dismissResult()">{{ t("fs.op.dismiss") }}</v-btn>
        </div>
        <ErrorAlert v-if="state.action.phase === 'error' && state.action.error" :error="state.action.error" :title="t('fs.op.actionFailed')" testid="fsetup-action-error" />
        <p v-if="op.status === 'cancelling'" class="text-body-2">{{ t("fs.op.cancelling") }}</p>
        <v-alert v-if="op.status === 'succeeded'" type="success" density="compact" data-testid="fsetup-succeeded">{{ t("fs.op.succeeded") }}</v-alert>
        <p v-if="state.defaults.phase === 'created'" data-testid="fsetup-defaults-created">{{ t("fs.defaults.created") }}</p>
        <p v-if="state.defaults.phase === 'kept'" data-testid="fsetup-defaults-kept">{{ t("fs.defaults.kept") }}</p>
        <ErrorAlert
          v-if="state.defaults.phase === 'error' && state.defaults.error"
          :error="state.defaults.error"
          :title="t('fs.defaults.failed')"
          testid="fsetup-defaults-error"
          :retry-label="t('fs.defaults.retry')"
          :on-retry="() => setup.retryDefaults()"
        />
      </section>

      <!-- 5. readiness -->
      <section class="u-stack" aria-labelledby="fsetup-ready" data-testid="fsetup-readiness">
        <h2 id="fsetup-ready" class="text-subtitle-1">{{ t("fs.ready.title") }}</h2>
        <p class="text-body-2">{{ t("fs.ready.body") }}</p>
        <p v-if="!library.preset.item || !library.policy.item" class="text-body-2" data-testid="fsetup-ready-noselection">{{ t("fs.ready.noSelection") }}</p>
        <div v-if="state.readiness.phase === 'pending'" role="status"><v-progress-linear indeterminate color="primary" />{{ t("fs.ready.checking") }}</div>
        <v-alert v-if="readiness?.state === 'ready'" type="success" density="compact" data-testid="fsetup-ready-yes">
          {{ t("fs.ready.yes", { recipe: readiness.selected?.recipeId ?? "", device: readiness.selected?.device ?? "" }) }}
        </v-alert>
        <v-alert v-else-if="readiness" type="warning" density="compact" data-testid="fsetup-ready-no">
          <p>{{ t("fs.ready.no") }}</p>
          <ul>
            <li v-for="c in readiness.candidates" :key="c.recipeId + c.profileId">
              {{ c.recipeId }} / {{ c.profileId }}: {{ c.issues.map((i) => text("fs.issue", i.split(":")[1] ?? i)).join(t("common.listSeparator")) }}
            </li>
            <li v-for="code in readiness.reasonCodes" :key="code">{{ text("v1.reason", code, "fs.code.other") }}</li>
          </ul>
        </v-alert>
        <v-alert v-if="readiness?.state === 'ready' && readiness.reasonCodes.includes('memory_unknown')" type="warning" density="compact" data-testid="gsetup-memory-warning">
          {{ t("fs.ready.memoryWarning") }}
        </v-alert>
        <ErrorAlert v-if="state.readiness.phase === 'error' && state.readiness.error" :error="state.readiness.error" :title="t('fs.ready.failed')" testid="fsetup-ready-error" />
        <div class="u-row">
          <v-btn variant="outlined" :disabled="!library.preset.item || !library.policy.item || state.readiness.phase === 'pending'" data-testid="fsetup-ready-check" @click="setup.checkReadiness()">
            {{ t("fs.ready.check") }}
          </v-btn>
          <v-btn
            v-if="verifiable && !running"
            color="primary"
            :disabled="verifyBlocked !== null"
            data-testid="fsetup-verify"
            @click="setup.prepareVerification(verifiable.profileId as ProfileKind)"
          >
            {{ t("fs.ready.verify") }}
          </v-btn>
        </div>
        <p v-if="verifiable && verifyBlocked" class="text-body-2" data-testid="fsetup-verify-blocked">{{ text("fs.verifyBlocked", verifyBlocked) }}</p>
        <section v-if="state.mode === 'verify' && plan && !op" class="u-stack" data-testid="fsetup-verify-review">
          <v-checkbox
            v-if="plan.requiredAgreements.length"
            :label="t('fs.choice.agreement')"
            :model-value="choice.agreementAccepted"
            @update:model-value="(on: boolean | null) => setup.acceptVerificationAgreement(on === true)"
          />
          <p>{{ t("fs.verify.review", { model: plan.verification?.modelRef ?? "", profile: plan.profileKind }) }}</p>
          <div class="u-row">
            <v-btn color="primary" :disabled="!canStart" :loading="state.start.phase === 'pending'" data-testid="fsetup-verify-start" @click="setup.confirmAndStart()">{{ t("fs.verify.start") }}</v-btn>
            <v-btn variant="text" data-testid="fsetup-verify-cancel" @click="setup.setMode('standard')">{{ t("fs.review.discard") }}</v-btn>
          </div>
        </section>
      </section>
    </v-card-text>
  </v-card>
</template>
