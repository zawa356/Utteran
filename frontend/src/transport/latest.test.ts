import { describe, expect, it } from "vitest";
import { deferred } from "../test/fakeFetch";
import { LatestOnly } from "./latest";

describe("LatestOnly", () => {
  it("discards a late answer of an older run and aborts it", async () => {
    const latest = new LatestOnly();
    const slow = deferred<string>();
    const fast = deferred<string>();
    let slowSignal: AbortSignal | null = null;
    const first = latest.run((signal) => {
      slowSignal = signal;
      return slow.promise;
    });
    const second = latest.run(() => fast.promise);
    expect(slowSignal!.aborted).toBe(true);
    fast.resolve("cpu");
    slow.resolve("cuda");
    expect(await second).toEqual({ stale: false, value: "cpu" });
    expect(await first).toEqual({ stale: true });
  });

  it("swallows errors of superseded runs but rethrows the latest", async () => {
    const latest = new LatestOnly();
    const old = deferred<string>();
    const first = latest.run(() => old.promise);
    const second = latest.run(() => Promise.reject(new Error("latest failed")));
    old.reject(new Error("old failed"));
    expect(await first).toEqual({ stale: true });
    await expect(second).rejects.toThrow("latest failed");
  });

  it("cancel makes the in-flight answer stale", async () => {
    const latest = new LatestOnly();
    const pending = deferred<number>();
    const run = latest.run(() => pending.promise);
    latest.cancel();
    pending.resolve(1);
    expect(await run).toEqual({ stale: true });
  });
});
