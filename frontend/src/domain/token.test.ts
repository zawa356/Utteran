import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import type { TokenState } from "../api/legacy";
import { createFakeHost } from "../host/fake";
import { deferred, hanging, json, scriptedFetch } from "../test/fakeFetch";
import { tokenState, unusedApi } from "../test/legacyFixtures";
import { createCore } from "./core";
import { tokenInputValid, tokenView, TokenController } from "./token";

// Assembled at runtime so no token-shaped literal is committed (AGENTS.md privacy rule).
const SECRET = ["hf", "synthetic", "unit", "value"].join("_");

function serverToken(initial: TokenState = tokenState()) {
  let status = initial;
  const calls: string[] = [];
  const api = unusedApi({
    getToken: async () => {
      calls.push("GET");
      return status;
    },
    putToken: async () => {
      calls.push("PUT");
      status = { ...status, configured: true };
      return status;
    },
    deleteToken: async () => {
      calls.push("DELETE");
      status = { ...status, configured: false };
      return status;
    },
  });
  return { api, calls };
}

describe("TokenController", () => {
  it("GET shows presence only; PUT/DELETE apply the server's answer", async () => {
    const { api, calls } = serverToken();
    const token = new TokenController(api);
    expect(tokenView(token.store.get())).toBe("loading");
    await token.load();
    expect(tokenView(token.store.get())).toBe("missing");
    expect(await token.set(SECRET)).toBe(true);
    expect(tokenView(token.store.get())).toBe("configured");
    expect(await token.clear()).toBe(true);
    expect(tokenView(token.store.get())).toBe("missing");
    expect(calls).toEqual(["GET", "PUT", "DELETE"]);
  });

  it("a store failure is 'unavailable', never 'missing'", async () => {
    const { api } = serverToken(tokenState({ available: false }));
    const token = new TokenController(api);
    await token.load();
    expect(tokenView(token.store.get())).toBe("unavailable");
  });

  it("keyring 503 on PUT: error, status unchanged, nothing claimed", async () => {
    const api = unusedApi({
      getToken: async () => tokenState(),
      putToken: async () => {
        throw new ApiError({ kind: "unavailable", status: 503, retryable: true, detail: "OS keyring is unavailable" });
      },
    });
    const token = new TokenController(api);
    await token.load();
    expect(await token.set(SECRET)).toBe(false);
    const state = token.store.get();
    expect(state.action.phase).toBe("error");
    expect(state.action.error?.status).toBe(503);
    expect(tokenView(state)).toBe("missing");
  });

  it("timeout on DELETE: 'unknown' until an explicit re-read", async () => {
    let configured = true;
    const api = unusedApi({
      getToken: async () => tokenState({ configured }),
      deleteToken: async () => {
        configured = false; // the server did apply it
        throw new ApiError({ kind: "timeout", outcomeUnknown: true, retryable: true });
      },
    });
    const token = new TokenController(api);
    await token.load();
    expect(await token.clear()).toBe(false);
    expect(tokenView(token.store.get())).toBe("unknown");
    await token.load();
    expect(tokenView(token.store.get())).toBe("missing");
  });

  it("a second action while one is pending is refused (one request per click)", async () => {
    const gate = deferred<TokenState>();
    let puts = 0;
    const api = unusedApi({
      putToken: () => {
        puts += 1;
        return gate.promise;
      },
    });
    const token = new TokenController(api);
    const first = token.set(SECRET);
    expect(await token.set(SECRET)).toBe(false);
    expect(await token.clear()).toBe(false);
    gate.resolve(tokenState({ configured: true }));
    expect(await first).toBe(true);
    expect(puts).toBe(1);
  });

  it("blank or oversized input is not sent", async () => {
    const { api, calls } = serverToken();
    const token = new TokenController(api);
    expect(tokenInputValid("   ")).toBe(false);
    expect(tokenInputValid("x".repeat(4097))).toBe(false);
    expect(await token.set("  ")).toBe(false);
    expect(calls).toEqual([]);
  });

  it("portable: session-only after a restart shows 'missing' with the session flag", async () => {
    const { api } = serverToken(tokenState({ sessionOnly: true }));
    const token = new TokenController(api);
    await token.load();
    expect(tokenView(token.store.get())).toBe("missing");
    expect(token.store.get().status?.sessionOnly).toBe(true);
  });

  it("the value never reaches any store, the error or a host report — even on timeout", async () => {
    const host = createFakeHost();
    const fetch = scriptedFetch((call) => {
      if (call.init.method === "PUT") return () => hanging(call.init);
      return json(200, { configured: false, available: true, backend: "synthetic" });
    });
    const core = createCore({ fetch, host });
    await core.token.load();
    vi.useFakeTimers();
    const pending = core.token.set(SECRET);
    await vi.advanceTimersByTimeAsync(30_001); // PUT /api/token times out after 30 s
    vi.useRealTimers();
    expect(await pending).toBe(false);
    const state = core.token.store.get();
    expect(tokenView(state)).toBe("unknown");
    expect(state.action.error?.kind).toBe("timeout");
    const everything = JSON.stringify({
      core: [core.settings.store.get(), core.token.store.get(), core.environment.store.get(), core.shell.store.get()],
      token: state,
      error: [state.action.error?.message, state.action.error?.detail, state.action.error?.code],
      reports: host.reports,
    });
    expect(everything).not.toContain(SECRET);
  });
});
