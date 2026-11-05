import { computed } from "vue";
import { usePreview } from "../composables";
import { jobOf, normalInputId, overrideCount } from "../domain/demo";
import { environmentView, exampleAllowsStart } from "../domain/envExamples";

/** The single file Normal mode works on, its job and why "start" may be unavailable. */
export function useNormalFile() {
  const { state, t } = usePreview();
  const input = computed(() => {
    const id = normalInputId(state.value);
    return state.value.inputs.find((i) => i.id === id) ?? null;
  });
  const job = computed(() => (input.value ? jobOf(state.value, input.value.id) : null));
  const others = computed(() => Math.max(0, state.value.inputs.length - (input.value ? 1 : 0)));
  const overrides = computed(() => (input.value ? overrideCount(state.value, input.value.id) : 0));
  const blocked = computed(() => {
    if (state.value.inputsArea !== "ready") return t("start.blocked.area");
    if (!input.value) return t("start.blocked.noFile");
    if (!input.value.supported) return t("start.blocked.unsupported");
    if (state.value.envChecking) return t("start.blocked.envChecking");
    if (!exampleAllowsStart(environmentView(state.value.envExample))) return t("start.blocked.env");
    return null;
  });
  return { input, job, others, overrides, blocked };
}
