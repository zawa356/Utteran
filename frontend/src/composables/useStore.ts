import { onScopeDispose, type Ref, shallowRef } from "vue";
import type { ReadonlyStore } from "../domain/store";

/** Thin read adapter: a Vue ref that mirrors a framework-independent store. */
export function useStore<S>(store: ReadonlyStore<S>): Readonly<Ref<S>> {
  const state = shallowRef(store.get());
  const unsubscribe = store.subscribe((next) => {
    state.value = next;
  });
  onScopeDispose(unsubscribe);
  return state;
}
