import { onScopeDispose, shallowRef } from "vue";
import { createController, exposeForTests, type State } from "../../core/core";

const controller = createController();
exposeForTests(controller);

export function useCore() {
  const state = shallowRef<State>(controller.store.get());
  const stop = controller.store.subscribe(() => {
    state.value = controller.store.get();
  });
  onScopeDispose(stop);
  return { state, controller };
}
