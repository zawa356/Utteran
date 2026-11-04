/** Minimal framework-independent observable store. Vue reads it through a thin adapter. */
export type Listener<S> = (state: S) => void;

export interface ReadonlyStore<S> {
  get(): S;
  subscribe(listener: Listener<S>): () => void;
}

export class Store<S> implements ReadonlyStore<S> {
  private state: S;
  private readonly listeners = new Set<Listener<S>>();

  constructor(initial: S) {
    this.state = initial;
  }

  get(): S {
    return this.state;
  }

  set(next: S | ((current: S) => S)): void {
    const value = typeof next === "function" ? (next as (current: S) => S)(this.state) : next;
    if (Object.is(value, this.state)) return;
    this.state = value;
    for (const listener of [...this.listeners]) listener(value);
  }

  patch(partial: Partial<S>): void {
    this.set((current) => ({ ...current, ...partial }));
  }

  subscribe(listener: Listener<S>): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}
