import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { LegacyApi, LegacyEnvironment } from "../api/legacy";
import { deferred } from "../test/fakeFetch";
import { environment } from "../test/legacyFixtures";
import { EnvironmentController } from "./environment";

function api(handler: (profile: string | null, refresh: boolean, signal?: AbortSignal) => Promise<LegacyEnvironment>): LegacyApi {
  return {
    getEnvironment: handler,
    getSettings: () => Promise.reject(new Error("unused")),
    patchSettings: () => Promise.reject(new Error("unused")),
    openFolder: () => Promise.reject(new Error("unused")),
  };
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
    expect(state.requestedProfile).toBe("cpu"); // adopted from the server default
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
  });
});
