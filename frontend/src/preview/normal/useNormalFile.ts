import { computed } from "vue";
import { usePreview } from "../composables";
import { inputIssues, jobOf, needsDiarization, normalInputId } from "../domain/demo";
import { environmentView, exampleAllowsStart, primaryProfile } from "../domain/envExamples";

/**
 * The single file Normal mode works on, its job, why "start" may be unavailable and a short
 * environment note (CPU only). Inputs that exist only for Expert are simply not shown here.
 */
export function useNormalFile() {
  const { state, t } = usePreview();
  const inputId = computed(() => normalInputId(state.value));
  const input = computed(() => state.value.inputs.find((i) => i.id === inputId.value) ?? null);
  const job = computed(() => (input.value ? jobOf(state.value, input.value.id) : null));
  const env = computed(() => environmentView(state.value.envExample, state.value.diarizationReady));
  /** `setup`: offer the way to the setup page; `diarization`: offer "turn diarization off" as well. */
  const blocked = computed(() => {
    const none = { setup: false, diarization: false };
    if (state.value.inputsArea !== "ready") return { text: t("start.blocked.area"), ...none };
    if (!input.value) return { text: t("start.blocked.noFile"), ...none };
    if (!input.value.supported) return { text: t("start.blocked.unsupported"), ...none };
    if (state.value.envChecking) return { text: t("start.blocked.envChecking"), ...none };
    if (!exampleAllowsStart(env.value)) return { text: t("start.blocked.env"), ...none, setup: true };
    if (inputIssues(state.value, input.value.id).length > 0) return { text: t("start.blocked.invalid"), ...none };
    if (needsDiarization(state.value, input.value.id)) return { text: t("start.blocked.diarization"), setup: true, diarization: true };
    return null;
  });
  /** Decision-relevant only: the recommended (fictional) profile runs on the CPU, i.e. slowly. */
  const slowNote = computed(() => {
    const p = primaryProfile(env.value);
    return p && p.name === "cpu" && p.status !== "unavailable" ? t("start.cpuNote") : null;
  });
  /** A profile needs repair while another still works: say so once, with the way to repair it. */
  const repairNote = computed(() => (env.value.profiles.some((p) => p.status === "repair") ? t("start.repairNote") : null));
  return { inputId, input, job, blocked, slowNote, repairNote };
}
