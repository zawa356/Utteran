import { ApiError, defaultRetryable, kindForStatus } from "./errors";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export interface HttpRequest {
  method: HttpMethod;
  path: string;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  headers?: Record<string, string>;
  /** Per-call timeout; the client default applies otherwise. */
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface HttpResponse<T> {
  status: number;
  data: T;
  headers: Headers;
}

export interface HttpClient {
  request<T>(request: HttpRequest, parse: (raw: unknown) => T): Promise<HttpResponse<T>>;
}

export interface HttpClientOptions {
  fetch: FetchLike;
  defaultTimeoutMs?: number;
}

export const SAFE_METHODS: ReadonlySet<HttpMethod> = new Set(["GET"]);

function buildUrl(path: string, query: HttpRequest["query"]): string {
  if (!path.startsWith("/api/")) throw new Error(`API path must start with /api/: ${path}`);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

interface ErrorBody {
  code: string | null;
  retryable: boolean | null;
  requestId: string | null;
  detail: string | null;
}

/** v1 envelope `{error:{code,retryable,request_id}}` or legacy `{detail}`; anything else is ignored. */
export function parseErrorBody(raw: unknown): ErrorBody {
  const empty: ErrorBody = { code: null, retryable: null, requestId: null, detail: null };
  if (typeof raw !== "object" || raw === null) return empty;
  const record = raw as Record<string, unknown>;
  const error = record.error;
  if (typeof error === "object" && error !== null) {
    const e = error as Record<string, unknown>;
    return {
      code: typeof e.code === "string" ? e.code : null,
      retryable: typeof e.retryable === "boolean" ? e.retryable : null,
      requestId: typeof e.request_id === "string" ? e.request_id : null,
      detail: null,
    };
  }
  if (typeof record.detail === "string") return { ...empty, detail: record.detail.slice(0, 500) };
  if (Array.isArray(record.detail)) return { ...empty, code: "request_invalid" };
  return empty;
}

async function readJson(response: Response): Promise<{ ok: true; value: unknown } | { ok: false }> {
  const text = await response.text();
  if (text === "") return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

/**
 * fetch wrapper: same-origin cookie session, timeout + caller abort, classified errors.
 * It never retries by itself — unsafe methods especially must not be resent implicitly.
 */
export function createHttpClient(options: HttpClientOptions): HttpClient {
  const defaultTimeout = options.defaultTimeoutMs ?? 15_000;
  return {
    async request<T>(request: HttpRequest, parse: (raw: unknown) => T): Promise<HttpResponse<T>> {
      const safe = SAFE_METHODS.has(request.method);
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, request.timeoutMs ?? defaultTimeout);
      const onAbort = () => controller.abort();
      if (request.signal?.aborted) controller.abort();
      request.signal?.addEventListener("abort", onAbort, { once: true });
      const headers: Record<string, string> = { Accept: "application/json", ...request.headers };
      let body: string | undefined;
      if (request.body !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(request.body);
      }
      try {
        let response: Response;
        try {
          response = await options.fetch(buildUrl(request.path, request.query), {
            method: request.method,
            headers,
            body,
            credentials: "same-origin",
            cache: "no-store",
            signal: controller.signal,
          });
        } catch {
          const kind = timedOut ? "timeout" : request.signal?.aborted ? "aborted" : "network";
          throw new ApiError({
            kind,
            retryable: kind !== "aborted",
            // A POST/PATCH may have been applied even though no answer arrived.
            outcomeUnknown: !safe && kind !== "aborted",
          });
        }
        const parsed = await readJson(response).catch(() => {
          const kind = timedOut ? "timeout" : request.signal?.aborted ? "aborted" : "network";
          throw new ApiError({ kind, status: response.status, outcomeUnknown: !safe && kind !== "aborted", retryable: kind !== "aborted" });
        });
        if (!response.ok) {
          const info = parsed.ok ? parseErrorBody(parsed.value) : parseErrorBody(null);
          const kind = kindForStatus(response.status);
          throw new ApiError({
            kind,
            status: response.status,
            code: info.code,
            retryable: info.retryable ?? defaultRetryable(kind, safe),
            requestId: info.requestId,
            detail: info.detail,
          });
        }
        if (!parsed.ok) {
          throw new ApiError({ kind: "malformed", status: response.status, retryable: safe });
        }
        let data: T;
        try {
          data = parse(parsed.value);
        } catch {
          throw new ApiError({ kind: "malformed", status: response.status, retryable: safe });
        }
        return { status: response.status, data, headers: response.headers };
      } finally {
        clearTimeout(timer);
        request.signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}
