import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { TokenState } from "../api/legacy";
import { deferred } from "../test/fakeFetch";
import { tokenState, unusedApi } from "../test/legacyFixtures";
import { TokenController, tokenView } from "./token";

/**
 * PR #40 review P1-1 / P1-2: "what is stored now" (GET, presence only) and "did my last
 * PUT/DELETE take effect" are different questions. A GET can answer the first, never the second.
 */
const SECRET = ["hf", "synthetic", "review", "a"].join("_");
const lost = () => new ApiError({ kind: "network", outcomeUnknown: true, retryable: true });
const store503 = () => new ApiError({ kind: "unavailable", status: 503, retryable: true, detail: "OS keyring is unavailable" });

/** A server whose token slot is `stored`; `apply` decides whether a mutation reaches it before `fail` is thrown. */
function server(initial: boolean) {
  let stored = initial;
  const control = { apply: true, fail: null as null | (() => ApiError), getFails: false };
  const api = unusedApi({
    getToken: async () => {
      if (control.getFails) throw new ApiError({ kind: "network", retryable: true });
      return tokenState({ configured: stored });
    },
    putToken: async () => {
      if (control.apply) stored = true;
      if (control.fail) throw control.fail();
      return tokenState({ configured: stored });
    },
    deleteToken: async () => {
      if (control.apply) stored = false;
      if (control.fail) throw control.fail();
      return tokenState({ configured: stored });
    },
  });
  return {
    api,
    control,
    get stored() {
      return stored;
    },
  };
}

describe("TokenController — review P1-1 (unconfirmed mutation vs current presence)", () => {
  it("missing → PUT applied, answer lost → GET configured: current presence shown, last PUT still unconfirmed, no stale error", async () => {
    const s = server(false);
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    expect(await token.set(SECRET)).toBe(false);
    expect(tokenView(token.store.get())).toBe("unknown");
    expect(token.store.get().unconfirmed).toEqual({ kind: "set", reason: "no_response" });
    await token.load();
    const state = token.store.get();
    expect(tokenView(state)).toBe("configured");
    expect(state.unconfirmed).toEqual({ kind: "set", reason: "no_response" }); // GET cannot prove the PUT
    expect(state.action.phase).toBe("idle"); // no old error next to the new status
    expect(state.action.error).toBeNull();
  });

  it("configured → PUT of another value NOT applied, answer lost → GET configured: never reported as replaced", async () => {
    const s = server(true);
    s.control.apply = false;
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    await token.set(SECRET);
    await token.load();
    const state = token.store.get();
    expect(tokenView(state)).toBe("configured");
    expect(state.unconfirmed?.kind).toBe("set");
    expect(state.action.phase).not.toBe("success");
  });

  it("configured → PUT applied, answer lost → GET configured: same answer (value equality is not inferred)", async () => {
    const s = server(true);
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    await token.set(SECRET);
    await token.load();
    expect(token.store.get().unconfirmed?.kind).toBe("set");
    expect(token.store.get().action.phase).not.toBe("success");
  });

  it("unconfirmed → failed GET keeps 'unknown'; a later successful GET settles only the current state", async () => {
    const s = server(false);
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    await token.set(SECRET);
    s.control.getFails = true;
    await token.load();
    expect(tokenView(token.store.get())).toBe("unknown");
    s.control.getFails = false;
    await token.load();
    expect(tokenView(token.store.get())).toBe("configured");
    expect(token.store.get().unconfirmed).not.toBeNull();
  });

  it("DELETE answer lost → GET missing: 'missing now', the delete itself stays unconfirmed", async () => {
    const s = server(true);
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    expect(await token.clear()).toBe(false);
    await token.load();
    expect(tokenView(token.store.get())).toBe("missing");
    expect(token.store.get().unconfirmed).toEqual({ kind: "clear", reason: "no_response" });
  });

  it("a later confirmed mutation clears the unconfirmed record; acknowledge() hides it explicitly", async () => {
    const s = server(false);
    s.control.fail = lost;
    const token = new TokenController(s.api);
    await token.load();
    await token.set(SECRET);
    await token.load();
    token.acknowledge();
    expect(token.store.get().unconfirmed).toBeNull();
    await token.set(SECRET);
    s.control.fail = null;
    await token.load();
    expect(await token.set(SECRET)).toBe(true);
    expect(token.store.get().unconfirmed).toBeNull();
    expect(token.store.get().action.phase).toBe("success");
  });

  it("an older GET answer never overwrites a newer mutation answer; double submit is refused", async () => {
    const slowGet = deferred<TokenState>();
    let gets = 0;
    let puts = 0;
    const token = new TokenController(
      unusedApi({
        getToken: () => (++gets === 1 ? Promise.resolve(tokenState()) : slowGet.promise),
        putToken: async () => {
          puts += 1;
          return tokenState({ configured: true });
        },
      }),
    );
    await token.load();
    const reload = token.load();
    expect(await token.set(SECRET)).toBe(true);
    expect(await Promise.all([token.set(SECRET), token.clear()]).then(() => puts)).toBe(2);
    slowGet.resolve(tokenState({ configured: false }));
    await reload;
    expect(tokenView(token.store.get())).toBe("configured");
  });

  it("a GET after a confirmed success does not repeat 'registered' (the message belongs to the action)", async () => {
    const s = server(false);
    s.control.fail = null;
    const token = new TokenController(s.api);
    await token.load();
    await token.set(SECRET);
    expect(token.store.get().action.phase).toBe("success");
    await token.load();
    expect(token.store.get().action.phase).toBe("idle");
  });
});

describe("TokenController — review P1-2 (503 is not 'unchanged')", () => {
  it("PUT 503 after the write happened (read-back failed): unconfirmed store_error, not a definitive failure", async () => {
    const s = server(false);
    s.control.fail = store503; // write applied, read-back failed → 503
    const token = new TokenController(s.api);
    await token.load();
    expect(await token.set(SECRET)).toBe(false);
    expect(s.stored).toBe(true);
    const state = token.store.get();
    expect(state.unconfirmed).toEqual({ kind: "set", reason: "store_error" });
    expect(tokenView(state)).toBe("unknown");
    await token.load();
    expect(tokenView(token.store.get())).toBe("configured");
    expect(token.store.get().unconfirmed?.reason).toBe("store_error");
  });

  it("DELETE 503 is handled the same way; a GET reporting the store unavailable wins over 'missing'", async () => {
    const api = unusedApi({
      getToken: async () => tokenState({ configured: false, available: false }),
      deleteToken: async () => {
        throw store503();
      },
    });
    const token = new TokenController(api);
    await token.load();
    await token.clear();
    expect(token.store.get().unconfirmed).toEqual({ kind: "clear", reason: "store_error" });
    await token.load();
    expect(tokenView(token.store.get())).toBe("unavailable");
  });

  it("a definitive client rejection (422/401) is an error, not 'unconfirmed'", async () => {
    const token = new TokenController(
      unusedApi({
        putToken: async () => {
          throw new ApiError({ kind: "validation", status: 422, code: "request_invalid" });
        },
      }),
    );
    await token.set(SECRET);
    expect(token.store.get().unconfirmed).toBeNull();
    expect(token.store.get().action.phase).toBe("error");
  });
});
