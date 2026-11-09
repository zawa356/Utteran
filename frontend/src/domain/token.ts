import { type LegacyApi, MAX_TOKEN_LENGTH, type TokenState } from "../api/legacy";
import { failed, idleOperation, type Operation, started, succeeded, toApiError } from "./operation";
import { Store } from "./store";

export type TokenAction = "set" | "clear";
/** What the settings page shows. `unavailable` (store failed) is never folded into `missing`. */
export type TokenView = "loading" | "load_error" | "configured" | "missing" | "unavailable" | "unknown";

export interface TokenControllerState {
  /** Last status the server reported (presence only — there is no value anywhere in here). */
  status: TokenState | null;
  load: Operation;
  action: Operation;
  actionKind: TokenAction | null;
  /** A PUT/DELETE got no answer: the shown status is not trusted until it is re-read. */
  outcomeUnknown: boolean;
}

export function tokenView(state: TokenControllerState): TokenView {
  if (state.outcomeUnknown) return "unknown";
  if (state.status === null) return state.load.phase === "error" ? "load_error" : "loading";
  if (!state.status.available) return "unavailable";
  return state.status.configured ? "configured" : "missing";
}

/** Client-side check mirroring TokenPayload (1..4096) and TokenStore.set (not blank). */
export function tokenInputValid(value: string): boolean {
  return value.trim() !== "" && value.length <= MAX_TOKEN_LENGTH;
}

/**
 * Hugging Face token through legacy GET/PUT/DELETE /api/token. The value is a parameter of
 * `set()` only: it is never put into this store, any other store, storage, logs or errors.
 * Only the server's answer changes the shown state; an unanswered request becomes "unknown".
 */
export class TokenController {
  readonly store = new Store<TokenControllerState>({
    status: null,
    load: idleOperation,
    action: idleOperation,
    actionKind: null,
    outcomeUnknown: false,
  });
  private loadGeneration = 0;

  constructor(private readonly api: LegacyApi) {}

  async load(): Promise<void> {
    const state = this.store.get();
    if (state.action.phase === "pending") return;
    const mine = ++this.loadGeneration;
    this.store.patch({ load: started(state.load) });
    try {
      const status = await this.api.getToken();
      if (mine !== this.loadGeneration) return;
      this.store.patch({ status, outcomeUnknown: false, load: succeeded(this.store.get().load) });
    } catch (error) {
      if (mine !== this.loadGeneration) return;
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }

  /** Returns true only when the server confirmed the new status. */
  set(value: string): Promise<boolean> {
    if (!tokenInputValid(value)) return Promise.resolve(false);
    return this.run("set", () => this.api.putToken(value));
  }

  /** Callers must have asked the person to confirm first. */
  clear(): Promise<boolean> {
    return this.run("clear", () => this.api.deleteToken());
  }

  private async run(kind: TokenAction, work: () => Promise<TokenState>): Promise<boolean> {
    const state = this.store.get();
    if (state.action.phase === "pending") return false;
    this.loadGeneration += 1; // an older GET answer must not overwrite the mutation's answer
    this.store.patch({ action: started(state.action), actionKind: kind, load: state.load.phase === "pending" ? idleOperation : state.load });
    try {
      const status = await work();
      this.store.patch({ status, outcomeUnknown: false, action: succeeded(this.store.get().action) });
      return true;
    } catch (error) {
      const apiError = toApiError(error);
      this.store.patch({
        action: failed(this.store.get().action, apiError),
        outcomeUnknown: apiError.outcomeUnknown || this.store.get().outcomeUnknown,
      });
      return false;
    }
  }
}
