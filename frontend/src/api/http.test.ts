import { afterEach, describe, expect, it, vi } from "vitest";
import { hanging, json, scriptedFetch, text } from "../test/fakeFetch";
import { ApiError } from "./errors";
import { createHttpClient } from "./http";

const id = (raw: unknown) => raw;

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected a failure");
}

describe("http client", () => {
  afterEach(() => vi.useRealTimers());

  it("returns parsed data and sends cookie-session same-origin requests without caching", async () => {
    const fetch = scriptedFetch([json(200, { ok: 1 })]);
    const http = createHttpClient({ fetch });
    const response = await http.request({ method: "GET", path: "/api/x", query: { a: "1", b: undefined } }, id);
    expect(response.data).toEqual({ ok: 1 });
    expect(fetch.calls[0]?.url).toBe("/api/x?a=1");
    expect(fetch.calls[0]?.init.credentials).toBe("same-origin");
    expect(fetch.calls[0]?.init.cache).toBe("no-store");
  });

  it("refuses paths outside /api/", async () => {
    const http = createHttpClient({ fetch: scriptedFetch([]) });
    await expect(http.request({ method: "GET", path: "//evil.example/api" }, id)).rejects.toThrow();
  });

  it.each([
    [401, text(401, "Unauthorized"), "unauthorized", null, false],
    [403, json(403, { schema_version: 1, error: { code: "origin_rejected", retryable: false, request_id: "r1" } }), "forbidden", "origin_rejected", false],
    [409, json(409, { detail: "ASR backend is unavailable" }), "conflict", null, false],
    [410, json(410, { schema_version: 1, error: { code: "event_cursor_expired", retryable: false } }), "gone", "event_cursor_expired", false],
    [422, json(422, { detail: [{ loc: ["body", "theme"], msg: "bad" }] }), "validation", "request_invalid", false],
    [428, json(428, { schema_version: 1, error: { code: "if_match_required", retryable: false } }), "precondition", "if_match_required", false],
    [429, json(429, {}), "rate_limited", null, true],
    [500, text(500, "Internal Server Error"), "server", null, true],
    [503, json(503, { schema_version: 1, error: { code: "store_busy", retryable: true } }), "unavailable", "store_busy", true],
  ] as const)("classifies HTTP %i", async (_status, reply, kind, code, retryable) => {
    const http = createHttpClient({ fetch: scriptedFetch([reply]) });
    const error = await failure(http.request({ method: "GET", path: "/api/x" }, id));
    expect(error.kind).toBe(kind);
    expect(error.code).toBe(code);
    expect(error.retryable).toBe(retryable);
    expect(error.outcomeUnknown).toBe(false);
  });

  it("keeps legacy detail text as secondary detail", async () => {
    const http = createHttpClient({ fetch: scriptedFetch([json(409, { detail: "ASR model is unavailable" })]) });
    const error = await failure(http.request({ method: "GET", path: "/api/x" }, id));
    expect(error.detail).toBe("ASR model is unavailable");
  });

  it("does not offer retry for a 500 answer to an unsafe method", async () => {
    const http = createHttpClient({ fetch: scriptedFetch([text(500, "")]) });
    const error = await failure(http.request({ method: "PATCH", path: "/api/x", body: {} }, id));
    expect(error.retryable).toBe(false);
  });

  it("reports broken JSON and failed shape checks as malformed", async () => {
    const broken = new Response("{not json", { status: 200 });
    const http = createHttpClient({ fetch: scriptedFetch([broken, json(200, { a: 1 })]) });
    expect((await failure(http.request({ method: "GET", path: "/api/x" }, id))).kind).toBe("malformed");
    const strict = (raw: unknown) => {
      if (!(raw as { b?: unknown }).b) throw new Error("shape");
      return raw;
    };
    expect((await failure(http.request({ method: "GET", path: "/api/x" }, strict))).kind).toBe("malformed");
  });

  it("times out a hung GET and never resends it", async () => {
    vi.useFakeTimers();
    const fetch = scriptedFetch(({ init }) => () => hanging(init));
    const http = createHttpClient({ fetch, defaultTimeoutMs: 1000 });
    const pending = failure(http.request({ method: "GET", path: "/api/x" }, id));
    await vi.advanceTimersByTimeAsync(1001);
    const error = await pending;
    expect(error.kind).toBe("timeout");
    expect(error.retryable).toBe(true);
    expect(error.outcomeUnknown).toBe(false);
    expect(fetch.calls).toHaveLength(1);
  });

  it("marks a POST without answer as outcome-unknown (ambiguous), sent exactly once", async () => {
    vi.useFakeTimers();
    const fetch = scriptedFetch(({ init }) => () => hanging(init));
    const http = createHttpClient({ fetch });
    const pending = failure(http.request({ method: "POST", path: "/api/x", body: { a: 1 }, timeoutMs: 500 }, id));
    await vi.advanceTimersByTimeAsync(501);
    const error = await pending;
    expect(error.kind).toBe("timeout");
    expect(error.outcomeUnknown).toBe(true);
    expect(fetch.calls).toHaveLength(1);
    expect(fetch.calls[0]?.init.body).toBe('{"a":1}');
  });

  it("network failure of an unsafe method is ambiguous, of a GET is not", async () => {
    const http = createHttpClient({ fetch: scriptedFetch([new TypeError("offline"), new TypeError("offline")]) });
    const post = await failure(http.request({ method: "POST", path: "/api/x", body: {} }, id));
    expect([post.kind, post.outcomeUnknown]).toEqual(["network", true]);
    const get = await failure(http.request({ method: "GET", path: "/api/x" }, id));
    expect([get.kind, get.outcomeUnknown]).toEqual(["network", false]);
  });

  it("distinguishes a caller abort from a timeout", async () => {
    const fetch = scriptedFetch(({ init }) => () => hanging(init));
    const http = createHttpClient({ fetch });
    const controller = new AbortController();
    const pending = failure(http.request({ method: "POST", path: "/api/x", body: {}, signal: controller.signal }, id));
    controller.abort();
    const error = await pending;
    expect(error.kind).toBe("aborted");
    expect(error.outcomeUnknown).toBe(false);
    expect(error.retryable).toBe(false);
  });
});
