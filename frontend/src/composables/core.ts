import { computed, inject, type InjectionKey } from "vue";
import type { Language } from "../api/legacy";
import type { AppCore } from "../domain/core";
import { type MessageKey, translate } from "../i18n";
import { useStore } from "./useStore";

export const CORE: InjectionKey<AppCore> = Symbol("utteran-core");

export function useCore(): AppCore {
  const core = inject(CORE);
  if (!core) throw new Error("AppCore was not provided");
  return core;
}

/** The UI language follows the *saved* legacy setting (ja until it is loaded). */
export function useI18n() {
  const settings = useStore(useCore().settings.store);
  const language = computed<Language>(() => settings.value.saved?.language ?? "ja");
  const t = (key: MessageKey, params?: Record<string, string | number>) => translate(language.value, key, params);
  return { language, t };
}
