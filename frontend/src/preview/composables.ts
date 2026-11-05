import { inject, type InjectionKey } from "vue";
import { useStore } from "../composables/useStore";
import type { ShellController } from "../domain/shell";
import type { DemoController } from "./domain/demo";
import { type PreviewKey, translatePreview } from "./i18n";

/** The prototype's own state: never the production AppCore (no api/, no host/). */
export interface PreviewContext {
  demo: DemoController;
  shell: ShellController;
}
export const PREVIEW: InjectionKey<PreviewContext> = Symbol("utteran-ui3-preview");

export function usePreview() {
  const ctx = inject(PREVIEW);
  if (!ctx) throw new Error("PreviewContext was not provided");
  const state = useStore(ctx.demo.store);
  const shellState = useStore(ctx.shell.store);
  const t = (key: PreviewKey, params?: Record<string, string | number>) => translatePreview(state.value.language, key, params);
  return { demo: ctx.demo, shell: ctx.shell, state, shellState, t };
}
