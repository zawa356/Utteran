import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deferred } from "../test/fakeFetch";
import { SingleFlightPoller } from "./poller";
import { browserScheduler } from "./scheduler";

describe("SingleFlightPoller (legacy polling, UI-1 D-07)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function harness(durationMs: number, fail = () => false) {
    let inflight = 0;
    let maxInflight = 0;
    let calls = 0;
    const poller = new SingleFlightPoller({
      intervalMs: 1000,
      backoff: { initialMs: 1000, maxMs: 8000 },
      scheduler: browserScheduler,
      task: async () => {
        calls += 1;
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        await new Promise((resolve) => setTimeout(resolve, durationMs));
        inflight -= 1;
        if (fail()) throw new Error("503");
      },
    });
    return { poller, stats: () => ({ calls, maxInflight }) };
  }

  it("never overlaps even when a request takes longer than the interval", async () => {
    const { poller, stats } = harness(3500);
    poller.start();
    await vi.advanceTimersByTimeAsync(20_000);
    poller.stop();
    expect(stats().maxInflight).toBe(1);
    // 3.5 s request + 1 s wait => a new request every 4.5 s.
    expect(stats().calls).toBe(5);
  });

  it("keeps failures visible and backs off", async () => {
    const { poller, stats } = harness(10, () => true);
    poller.start();
    await vi.advanceTimersByTimeAsync(10);
    expect(poller.snapshot.consecutiveFailures).toBe(1);
    expect(poller.snapshot.lastError).toBeInstanceOf(Error);
    await vi.advanceTimersByTimeAsync(2000 + 10); // second attempt after 2 s backoff
    expect(stats().calls).toBe(2);
    await vi.advanceTimersByTimeAsync(4000 + 10);
    expect(stats().calls).toBe(3);
    poller.stop();
  });

  it("pollNow is coalesced while a request is in flight", async () => {
    const gate = deferred<void>();
    let calls = 0;
    const poller = new SingleFlightPoller({
      intervalMs: 1000,
      backoff: { initialMs: 1000, maxMs: 1000 },
      scheduler: browserScheduler,
      task: () => {
        calls += 1;
        return gate.promise;
      },
    });
    poller.start();
    poller.pollNow();
    poller.pollNow();
    expect(calls).toBe(1);
    gate.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(poller.snapshot.status).toBe("waiting");
    poller.pollNow();
    expect(calls).toBe(2);
    poller.stop();
  });

  it("pause stops scheduling until resume; stop is final", async () => {
    const { poller, stats } = harness(10);
    poller.start();
    await vi.advanceTimersByTimeAsync(10);
    poller.pause();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(stats().calls).toBe(1);
    poller.resume();
    await vi.advanceTimersByTimeAsync(10);
    expect(stats().calls).toBe(2);
    poller.stop();
    poller.start();
    poller.resume();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(stats().calls).toBe(2);
    expect(poller.snapshot.status).toBe("stopped");
  });

  it("pausing during a request does not start a parallel one on resume", async () => {
    const { poller, stats } = harness(500);
    poller.start();
    poller.pause();
    poller.resume();
    await vi.advanceTimersByTimeAsync(400);
    expect(stats()).toEqual({ calls: 1, maxInflight: 1 });
    await vi.advanceTimersByTimeAsync(1200);
    expect(stats().calls).toBe(2);
    expect(stats().maxInflight).toBe(1);
    poller.stop();
  });
});
