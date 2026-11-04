import type { FetchLike } from "../api/http";

export interface Call {
  url: string;
  init: RequestInit;
}

export type Reply = Response | Error | (() => Promise<Response>);

/** Scripted fetch: each call takes the next reply (or a routed one). Records every call. */
export function scriptedFetch(replies: Reply[] | ((call: Call) => Reply)): FetchLike & { calls: Call[] } {
  const calls: Call[] = [];
  const fn = (async (url: string, init: RequestInit) => {
    const call = { url, init };
    calls.push(call);
    const reply = typeof replies === "function" ? replies(call) : replies.shift();
    if (reply === undefined) throw new Error(`unexpected request ${url}`);
    if (reply instanceof Error) throw reply;
    if (typeof reply === "function") return reply();
    return reply;
  }) as FetchLike & { calls: Call[] };
  fn.calls = calls;
  return fn;
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

export function text(status: number, body: string): Response {
  return new Response(body, { status });
}

/** A fetch that never answers until its signal aborts (like a real hung request). */
export function hanging(init: RequestInit): Promise<Response> {
  return new Promise((_, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
