import { computed } from "vue";
import { usePreview } from "../composables";
import { type EnvFact, environmentView, primaryProfile } from "../domain/envExamples";
import { isPreviewKey } from "../i18n";

/** View helpers for the fictional environment examples (values with `key:` are translated). */
export function useEnvironment() {
  const { state, t } = usePreview();
  const view = computed(() => environmentView(state.value.envExample));
  const primary = computed(() => primaryProfile(view.value));
  const value = (fact: EnvFact) => {
    if (!fact.value.startsWith("key:")) return fact.value;
    const key = fact.value.slice(4);
    return isPreviewKey(key) ? t(key) : key;
  };
  const fact = (label: EnvFact["label"]) => {
    const f = primary.value?.facts.find((x) => x.label === label);
    return f ? value(f) : null;
  };
  return { view, primary, value, fact, checking: computed(() => state.value.envChecking) };
}
