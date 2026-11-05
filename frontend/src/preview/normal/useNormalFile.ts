import { computed } from "vue";
import { usePreview } from "../composables";
import { jobOf, normalInputId } from "../domain/demo";
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
  const env = computed(() => environmentView(state.value.envExample));
  const blocked = computed(() => {
    if (state.value.inputsArea !== "ready") return { text: t("start.blocked.area"), setup: false };
    if (!input.value) return { text: t("start.blocked.noFile"), setup: false };
    if (!input.value.supported) return { text: t("start.blocked.unsupported"), setup: false };
    if (state.value.envChecking) return { text: t("start.blocked.envChecking"), setup: false };
    if (!exampleAllowsStart(env.value)) return { text: t("start.blocked.env"), setup: true };
    return null;
  });
  /** Decision-relevant only: the recommended (fictional) profile runs on the CPU, i.e. slowly. */
  const slowNote = computed(() => {
    const p = primaryProfile(env.value);
    return p && p.name === "cpu" && p.status !== "unavailable" ? t("start.cpuNote") : null;
  });
  return { inputId, input, job, blocked, slowNote };
}
