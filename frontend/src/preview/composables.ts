import { inject, type InjectionKey } from "vue";
import { useStore } from "../composables/useStore";
import type { DemoController } from "./domain/demo";
import type { LayoutController } from "./domain/layout";
import type { SetupController } from "./domain/setup";
import { type PreviewKey, translatePreview } from "./i18n";

/** The prototype's own state: never the production AppCore (no api/, no host/). */
export interface PreviewContext {
  demo: DemoController;
  layout: LayoutController;
  setup: SetupController;
}
export const PREVIEW: InjectionKey<PreviewContext> = Symbol("utteran-ui3-preview");

export function usePreview() {
  const ctx = inject(PREVIEW);
  if (!ctx) throw new Error("PreviewContext was not provided");
  const state = useStore(ctx.demo.store);
  const layoutState = useStore(ctx.layout.store);
  const setupState = useStore(ctx.setup.store);
  const t = (key: PreviewKey, params?: Record<string, string | number>) => translatePreview(state.value.language, key, params);
  return { demo: ctx.demo, layout: ctx.layout, setup: ctx.setup, state, layoutState, setupState, t };
}
