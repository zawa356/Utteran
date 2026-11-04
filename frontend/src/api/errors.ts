/**
 * Classified API failures. Views never inspect raw HTTP: they read `kind`, `code`
 * and `retryable`, and i18n turns those into user text (unknown codes fall back by kind).
 */
export type ApiErrorKind =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "gone"
  | "precondition"
  | "validation"
  | "rate_limited"
  | "server"
  | "unavailable"
  | "timeout"
  | "aborted"
  | "network"
  | "malformed";

export interface ApiErrorInit {
  kind: ApiErrorKind;
  status?: number | null;
  code?: string | null;
  retryable?: boolean;
  requestId?: string | null;
  /** True when an unsafe request may have reached the server but no answer arrived. */
  outcomeUnknown?: boolean;
  detail?: string | null;
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly retryable: boolean;
  readonly requestId: string | null;
  readonly outcomeUnknown: boolean;
  /** Legacy `detail` text (already server-side redacted). Shown only as secondary detail. */
  readonly detail: string | null;

  constructor(init: ApiErrorInit) {
    super(init.code ?? init.kind);
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status ?? null;
    this.code = init.code ?? null;
    this.retryable = init.retryable ?? false;
    this.requestId = init.requestId ?? null;
    this.outcomeUnknown = init.outcomeUnknown ?? false;
    this.detail = init.detail ?? null;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 410) return "gone";
  if (status === 412 || status === 428) return "precondition";
  if (status === 400 || status === 413 || status === 422) return "validation";
  if (status === 429) return "rate_limited";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "server";
}

/** Whether a person may sensibly press "retry" (never an automatic resend). */
export function defaultRetryable(kind: ApiErrorKind, safeMethod: boolean): boolean {
  switch (kind) {
    case "rate_limited":
    case "unavailable":
    case "timeout":
    case "network":
      return true;
    case "server":
    case "malformed":
      return safeMethod;
    default:
      return false;
  }
}
