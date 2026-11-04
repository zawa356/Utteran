import { ApiError, isApiError } from "../api/errors";

/**
 * Per-operation phase (not per screen). `accepted` = the server took an asynchronous
 * request (202) that is not finished yet; it is never shown as success.
 */
export type Phase = "idle" | "pending" | "accepted" | "success" | "error" | "cancelled";

export interface Operation {
  phase: Phase;
  error: ApiError | null;
  /** Incremented on every start, so views can tell attempts apart. */
  attempt: number;
}

export const idleOperation: Operation = { phase: "idle", error: null, attempt: 0 };

export function started(previous: Operation): Operation {
  return { phase: "pending", error: null, attempt: previous.attempt + 1 };
}

export function succeeded(previous: Operation): Operation {
  return { phase: "success", error: null, attempt: previous.attempt };
}

export function failed(previous: Operation, error: unknown): Operation {
  return { phase: "error", error: toApiError(error), attempt: previous.attempt };
}

export function cancelled(previous: Operation): Operation {
  return { phase: "cancelled", error: null, attempt: previous.attempt };
}

export function toApiError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  return new ApiError({ kind: "malformed", code: "client_error" });
}
