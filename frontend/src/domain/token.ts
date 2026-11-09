import type { ApiError } from "../api/errors";
import { type LegacyApi, MAX_TOKEN_LENGTH, type TokenState } from "../api/legacy";
import { failed, idleOperation, type Operation, started, succeeded, toApiError } from "./operation";
import { Store } from "./store";

export type TokenAction = "set" | "clear";
/**
 * What the status line shows: the token's *current presence* as last read from the server.
 * `unavailable` (store failed) is never folded into `missing`; `unknown` = not read since a
 * mutation whose result is unconfirmed.
 */
export type TokenView = "loading" | "load_error" | "configured" | "missing" | "unavailable" | "unknown";

/**
 * Why the last PUT/DELETE has no confirmed result:
 * - `no_response`: timeout/network — the request may or may not have reached the server.
 * - `store_error`: the server answered 5xx (e.g. 503 from TokenStore). `TokenStore.set()` writes
 *   first and then reads back, so a 503 can follow a write that did happen.
 */
export type UnconfirmedReason = "no_response" | "store_error";

export interface UnconfirmedMutation {
  kind: TokenAction;
  reason: UnconfirmedReason;
}

export interface TokenControllerState {
  /** Last presence the server reported (there is no value anywhere in here). */
  status: TokenState | null;
  /** false after an unconfirmed mutation until a GET succeeds: then `status` is not current. */
  statusCurrent: boolean;
  load: Operation;
  /** The last PUT/DELETE as an operation: `success` only for a 2xx answer to that request. */
  action: Operation;
  actionKind: TokenAction | null;
  /**
   * The last mutation's effect is unknown. A later GET tells the current presence but can never
   * prove that the PUT replaced the value (presence only) or that the DELETE caused "missing",
   * so a GET does not clear this. Cleared by a confirmed mutation or an explicit acknowledge().
   */
  unconfirmed: UnconfirmedMutation | null;
}

export function tokenView(state: TokenControllerState): TokenView {
  if (state.status === null) return state.load.phase === "error" ? "load_error" : state.statusCurrent ? "loading" : "unknown";
  if (!state.statusCurrent) return "unknown";
  if (!state.status.available) return "unavailable";
  return state.status.configured ? "configured" : "missing";
}

/** Client-side check mirroring TokenPayload (1..4096) and TokenStore.set (not blank). */
export function tokenInputValid(value: string): boolean {
  return value.trim() !== "" && value.length <= MAX_TOKEN_LENGTH;
}

/** No answer, or a server-side failure that may have happened after the keyring write. */
export function isUnconfirmed(error: ApiError): UnconfirmedReason | null {
  if (error.outcomeUnknown) return "no_response";
  if (error.kind === "server" || error.kind === "unavailable") return "store_error";
  return null;
}

/**
 * Hugging Face token through legacy GET/PUT/DELETE /api/token. The value is a parameter of
 * `set()` only: it is never put into this store, any other store, storage, logs or errors.
 * Only a server answer changes the shown presence; a mutation without a confirmed answer is
 * recorded as unconfirmed and is never retried automatically.
 */
export class TokenController {
  readonly store = new Store<TokenControllerState>({
    status: null,
    statusCurrent: true,
    load: idleOperation,
    action: idleOperation,
    actionKind: null,
    unconfirmed: null,
  });
  private loadGeneration = 0;

  constructor(private readonly api: LegacyApi) {}

  /** Read the current presence. Answers the "now" question only (see `unconfirmed`). */
  async load(): Promise<void> {
    const state = this.store.get();
    if (state.action.phase === "pending") return;
    const mine = ++this.loadGeneration;
    this.store.patch({ load: started(state.load) });
    try {
      const status = await this.api.getToken();
      if (mine !== this.loadGeneration) return;
      // The previous action's message (success or error) belonged to that request; after a
      // fresh read only the current presence (+ any unconfirmed record) is shown.
      this.store.patch({ status, statusCurrent: true, action: idleOperation, actionKind: null, load: succeeded(this.store.get().load) });
    } catch (error) {
      if (mine !== this.loadGeneration) return;
      this.store.patch({ load: failed(this.store.get().load, error) });
    }
  }

  /** Returns true only when the server answered this PUT with a status. */
  set(value: string): Promise<boolean> {
    if (!tokenInputValid(value)) return Promise.resolve(false);
    return this.run("set", () => this.api.putToken(value));
  }

  /** Callers must have asked the person to confirm first. */
  clear(): Promise<boolean> {
    return this.run("clear", () => this.api.deleteToken());
  }

  /**
   * The person dismissed the "last action unconfirmed" note (it changes nothing on the server).
   * Only once the current presence has been read again; before that the note is the only thing
   * explaining why the state is unknown.
   */
  acknowledge(): void {
    if (this.store.get().statusCurrent) this.store.patch({ unconfirmed: null });
  }

  private async run(kind: TokenAction, work: () => Promise<TokenState>): Promise<boolean> {
    const state = this.store.get();
    if (state.action.phase === "pending") return false;
    this.loadGeneration += 1; // an older GET answer must not overwrite the mutation's answer
    this.store.patch({ action: started(state.action), actionKind: kind, load: state.load.phase === "pending" ? idleOperation : state.load });
    try {
      const status = await work();
      this.store.patch({ status, statusCurrent: true, unconfirmed: null, action: succeeded(this.store.get().action) });
      return true;
    } catch (error) {
      const apiError = toApiError(error);
      const reason = isUnconfirmed(apiError);
      this.store.patch({
        action: failed(this.store.get().action, apiError),
        ...(reason ? { unconfirmed: { kind, reason }, statusCurrent: false } : {}),
      });
      return false;
    }
  }
}
