/**
 * Latest-wins for reads (UI-1 D-01): each run aborts the previous one and only the
 * newest generation may publish. A late answer of an older run is reported as stale,
 * even if it resolved successfully, so it can never overwrite a newer selection.
 */
export type LatestResult<T> = { stale: false; value: T } | { stale: true };

export class LatestOnly {
  private generation = 0;
  private controller: AbortController | null = null;

  get current(): number {
    return this.generation;
  }

  async run<T>(work: (signal: AbortSignal) => Promise<T>): Promise<LatestResult<T>> {
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const mine = ++this.generation;
    try {
      const value = await work(controller.signal);
      return mine === this.generation ? { stale: false, value } : { stale: true };
    } catch (error) {
      if (mine !== this.generation) return { stale: true };
      throw error;
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }

  /** Abort the in-flight run; its result (if any) is then stale. */
  cancel(): void {
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
  }
}
