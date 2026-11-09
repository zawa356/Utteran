import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { createHttpClient } from "../api/http";
import { createLegacyApi, ENVIRONMENT_TIMEOUT_MS, type LegacyApi, type LegacyEnvironment } from "../api/legacy";
import { deferred, hanging, scriptedFetch } from "../test/fakeFetch";
import { environment, unusedApi } from "../test/legacyFixtures";
import { EnvironmentController, environmentView, isCurrentResult } from "./environment";

function api(handler: (profile: string | null, refresh: boolean, signal?: AbortSignal) => Promise<LegacyEnvironment>): LegacyApi {
  return unusedApi({ getEnvironment: handler });
}

describe("EnvironmentController", () => {
  it("UI-1 D-01: a late answer for an earlier profile never rolls the selection back", async () => {
    const slow = deferred<LegacyEnvironment>();
    const signals: AbortSignal[] = [];
    const controller = new EnvironmentController(
      api((profile, _refresh, signal) => {
        if (signal) signals.push(signal);
        return profile === "cuda" ? slow.promise : Promise.resolve(environment("cpu"));
      }),
    );
    const first = controller.select("cuda");
    expect(controller.store.get().requestedProfile).toBe("cuda");
    const second = controller.select("cpu");
    await second;
    slow.resolve(environment("cuda"));
    await first;
    const state = controller.store.get();
    expect(state.requestedProfile).toBe("cpu");
    expect(state.data?.activeProfile).toBe("cpu");
    expect(state.load.phase).toBe("success");
    expect(state.staleDiscarded).toBe(1);
    expect(state.abandoned).toBe(1); // the cuda request was superseded (its server probe may still run)
    expect(signals[0]?.aborted).toBe(true);
  });

  it("shows loading, error with retry, then success; readiness stays unverified", async () => {
    let calls = 0;
    const controller = new EnvironmentController(
      api(async () => {
        calls += 1;
        if (calls === 1) throw new ApiError({ kind: "server", status: 500, retryable: true });
        return environment("cpu");
      }),
    );
    const pending = controller.select(null);
    expect(controller.store.get().load.phase).toBe("pending");
    await pending;
    expect(controller.store.get().load.phase).toBe("error");
    expect(controller.store.get().load.error?.retryable).toBe(true);
    await controller.retry();
    const state = controller.store.get();
    expect(state.load.phase).toBe("success");
    // S2: the request (none) and the server's choice (cpu) stay separate facts.
    expect(state.requestedProfile).toBeNull();
    expect(state.data?.activeProfile).toBe("cpu");
    expect(state.readiness).toBe("unverified");
  });

  it("redetect passes refresh and can be cancelled without publishing the late answer", async () => {
    const pending = deferred<LegacyEnvironment>();
    const seen: boolean[] = [];
    const controller = new EnvironmentController(
      api((_p, refresh) => {
        seen.push(refresh);
        return pending.promise;
      }),
    );
    const run = controller.redetect();
    controller.cancel();
    expect(controller.store.get().load.phase).toBe("cancelled");
    pending.resolve(environment("cuda"));
    await run;
    expect(seen).toEqual([true]);
    expect(controller.store.get().data).toBeNull();
    expect(controller.store.get().load.phase).toBe("cancelled");
    expect(controller.store.get().abandoned).toBe(1);
  });

  it("a late failure after cancel does not replace the cancelled state or the previous result", async () => {
    const late = deferred<LegacyEnvironment>();
    let call = 0;
    const controller = new EnvironmentController(api(() => (++call === 1 ? Promise.resolve(environment("cpu")) : late.promise)));
    await controller.select("cpu");
    const run = controller.select("cuda");
    controller.cancel();
    late.reject(new ApiError({ kind: "server", status: 500 }));
    await run;
    const state = controller.store.get();
    expect(state.load.phase).toBe("cancelled");
    expect(state.load.error).toBeNull();
    expect(state.data?.activeProfile).toBe("cpu"); // kept, but only as the previous result
    expect(isCurrentResult(state)).toBe(false);
    expect(state.requestedProfile).toBe("cuda");
  });

  it("retry after cancel repeats the same request, and only an explicit action does it", async () => {
    const seen: [string | null, boolean][] = [];
    const first = deferred<LegacyEnvironment>();
    const controller = new EnvironmentController(
      api((profile, refresh) => {
        seen.push([profile, refresh]);
        return seen.length === 1 ? first.promise : Promise.resolve(environment("cuda"));
      }),
    );
    void controller.select("cuda");
    await Promise.resolve();
    controller.redetect(); // same profile, now with refresh
    controller.cancel();
    expect(seen).toEqual([
      ["cuda", false],
      ["cuda", true],
    ]);
    await controller.retry();
    expect(seen.at(-1)).toEqual(["cuda", true]);
    expect(seen).toHaveLength(3); // no automatic retry anywhere
  });

  it("does not start a second server probe for the same in-flight request (double click)", async () => {
    const answer = deferred<LegacyEnvironment>();
    const seen: [string | null, boolean][] = [];
    const controller = new EnvironmentController(
      api((profile, refresh) => {
        seen.push([profile, refresh]);
        return answer.promise;
      }),
    );
    const a = controller.redetect();
    const b = controller.redetect();
    const c = controller.redetect();
    expect(seen).toEqual([[null, true]]);
    answer.resolve(environment("cpu"));
    await Promise.all([a, b, c]);
    expect(controller.store.get().load.phase).toBe("success");
    expect(controller.store.get().abandoned).toBe(0);
  });

  it("a pending or failed reload shows the old answer only as the previous result", async () => {
    let now = 1_000;
    const second = deferred<LegacyEnvironment>();
    let call = 0;
    const controller = new EnvironmentController(api(() => (++call === 1 ? Promise.resolve(environment("cpu")) : second.promise)), {
      now: () => now,
    });
    await controller.select("cpu");
    expect(isCurrentResult(controller.store.get())).toBe(true);
    expect(controller.store.get().dataFor).toMatchObject({ profile: "cpu", refresh: false, followsDefault: false, receivedAt: 1_000 });
    now = 5_000;
    const run = controller.select("cuda");
    let state = controller.store.get();
    expect(state.startedAt).toBe(5_000);
    expect(environmentView(state)).toBe("pending");
    expect(isCurrentResult(state)).toBe(false);
    expect(state.data?.activeProfile).toBe("cpu");
    second.reject(new ApiError({ kind: "timeout", retryable: true }));
    await run;
    state = controller.store.get();
    expect(environmentView(state)).toBe("error");
    expect(isCurrentResult(state)).toBe(false);
    expect(state.dataFor?.profile).toBe("cpu");
  });

  it("follow-default reads the saved default and is a different selection from a manual choice", async () => {
    const seen: (string | null)[] = [];
    let saved: string | null = "cuda";
    const controller = new EnvironmentController(
      api((profile) => {
        seen.push(profile);
        return Promise.resolve(environment(profile ?? "cpu"));
      }),
      { defaultProfile: () => saved },
    );
    await controller.selectDefault();
    expect(controller.store.get()).toMatchObject({ requestedProfile: "cuda", followsDefault: true });
    expect(isCurrentResult(controller.store.get())).toBe(true);
    await controller.select("cuda"); // the same profile chosen by hand is a new, manual selection
    expect(controller.store.get().followsDefault).toBe(false);
    saved = null;
    await controller.selectDefault();
    expect(seen).toEqual(["cuda", "cuda", null]);
    expect(controller.store.get().requestedProfile).toBeNull();
  });

  it("views: empty (no usable profile) and partial (answered with issues) are not errors", async () => {
    const answers = [
      environment("cpu", { activeProfile: null }),
      environment("cpu", { issues: [{ kind: "timeout", probe: "devices", seconds: 200 }] }),
    ];
    const controller = new EnvironmentController(api(() => Promise.resolve(answers.shift() as LegacyEnvironment)));
    await controller.select(null);
    expect(environmentView(controller.store.get())).toBe("empty");
    await controller.select("cpu");
    expect(environmentView(controller.store.get())).toBe("partial");
    expect(controller.store.get().readiness).toBe("unverified");
  });
});

describe("GET /api/environment timeout (fake clock; nothing waits 240 s)", () => {
  afterEach(() => vi.useRealTimers());

  it("is still waiting at 120 s, then fails as a retryable timeout at the limit", async () => {
    vi.useFakeTimers();
    const fetch = scriptedFetch((call) => () => hanging(call.init));
    const controller = new EnvironmentController(createLegacyApi(createHttpClient({ fetch })));
    const run = controller.select("cpu");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(controller.store.get().load.phase).toBe("pending");
    await vi.advanceTimersByTimeAsync(ENVIRONMENT_TIMEOUT_MS - 120_000);
    await run;
    const state = controller.store.get();
    expect(state.load.phase).toBe("error");
    expect(state.load.error).toMatchObject({ kind: "timeout", retryable: true, outcomeUnknown: false });
    expect(fetch.calls).toHaveLength(1);
    expect(fetch.calls[0]?.url).toBe("/api/environment?profile=cpu");
  });

  it("cancel aborts the fetch (it does not claim the server stopped) and sends nothing else", async () => {
    const fetch = scriptedFetch((call) => () => hanging(call.init));
    const controller = new EnvironmentController(createLegacyApi(createHttpClient({ fetch })));
    const run = controller.redetect();
    controller.cancel();
    await run;
    expect(fetch.calls).toHaveLength(1);
    expect(fetch.calls[0]?.url).toBe("/api/environment?refresh=true");
    expect(fetch.calls[0]?.init.method).toBe("GET");
    expect(fetch.calls[0]?.init.signal?.aborted).toBe(true);
    expect(controller.store.get()).toMatchObject({ abandoned: 1, load: { phase: "cancelled" } });
  });
});
