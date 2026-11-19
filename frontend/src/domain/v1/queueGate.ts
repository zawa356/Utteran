import { Store } from "../store";

/** One app-lifetime lease for every queue write. Unknown outcomes retain ownership. */
export class QueueMutationGate {
  readonly store = new Store<{ owner: symbol | null }>({ owner: null });
  busy(): boolean { return this.store.get().owner !== null; }
  owns(owner: symbol): boolean { return this.store.get().owner === owner; }
  acquire(owner: symbol): boolean {
    if (this.busy()) return false;
    this.store.set({ owner });
    return true;
  }
  release(owner: symbol): void {
    if (this.owns(owner)) this.store.set({ owner: null });
  }
}
