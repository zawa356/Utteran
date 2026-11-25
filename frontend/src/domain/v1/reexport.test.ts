import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createReexportApi, parseRecord, parseSource } from "../../api/reexport";
import { json, scriptedFetch, type Call, type Reply } from "../../test/fakeFetch";
import { V1ReexportController } from "./reexport";

const JOB = `job_${"a".repeat(32)}`;
const REX = `rex_${"b".repeat(32)}`;
const SHA = "c".repeat(64);
const NOW = "2026-11-25T00:00:00Z";

function source(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    job_id: JOB,
    job_revision: 4,
    available: true,
    refusal: null,
    source: { sha256: SHA, size: 100, newline: "lf", segments: 3, words: 5, speakers: ["SPEAKER_00", "Alice"], diarization: true },
    defaults: { formats: ["srt", "json", "md"], show_speaker: true, srt_bom: true, newline: "crlf" },
    original_labels_applied: true,
    default_parent: "/synthetic/out",
    limits: { max_source_bytes: 33554432, max_label: 256, max_speakers: 256 },
    ...overrides,
  };
}
function rec(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    id: REX,
    job_id: JOB,
    job_revision: 4,
    state: "completed",
    error_code: null,
    partial: false,
    created_at: NOW,
    updated_at: NOW,
    formats: ["srt"],
    options: { show_speaker: true, srt_bom: false, newline: "lf", speaker_label_count: 0, explicit_parent: false },
    source_sha256: SHA,
    directory: "/synthetic/out/x_reexport_20261125-000000_bbbbbbbb",
    files: [{ format: "srt", size: 10, sha256: SHA }],
    location_verified: true,
    ...overrides,
  };
}
const list = (items: unknown[] = []) => json(200, { schema_version: 1, job_id: JOB, items });

function setup(route: (call: Call) => Reply) {
  const fetch = scriptedFetch(route);
  const controller = new V1ReexportController(createReexportApi(createHttpClient({ fetch })));
  return { fetch, controller };
}
const posts = (calls: Call[]) => calls.filter(c => c.init.method === "POST");
const header = (call: Call, name: string) => new Headers(call.init.headers).get(name);
const body = (call: Call) => JSON.parse(String(call.init.body)) as Record<string, unknown>;

describe("E-C wire parsing", () => {
  it("accepts the source and record shapes and refuses inconsistent ones", () => {
    expect(parseSource(source()).source?.speakers).toEqual(["SPEAKER_00", "Alice"]);
    expect(parseSource(source({ available: false, refusal: "source_changed", source: null })).refusal).toBe("source_changed");
    expect(() => parseSource(source({ available: false }))).toThrow();
    expect(() => parseSource(source({ refusal: "x" }))).toThrow();
    expect(() => parseSource(source({ defaults: { formats: ["pdf"], show_speaker: true, srt_bom: false, newline: "lf" } }))).toThrow();
    expect(parseRecord(rec()).files[0]?.format).toBe("srt");
    expect(() => parseRecord(rec({ state: "done" }))).toThrow();
    expect(() => parseRecord(rec({ id: "rex_x" }))).toThrow();
  });
});

describe("V1ReexportController", () => {
  it("starts from the frozen output settings, never re-applies labels, and sends one intent", async () => {
    const { fetch, controller } = setup(call => call.init.method === "POST" ? json(201, rec()) : call.url.endsWith("/reexports") ? list() : json(200, source()));
    await controller.open(JOB);
    const state = controller.store.get();
    expect(state.step).toBe("form");
    expect(state.form).toEqual({ formats: ["srt", "json", "md"], showSpeaker: true, srtBom: true, newline: "crlf", labels: {}, parent: null });
    controller.setLabel("Alice", "Bob");
    controller.setLabel("SPEAKER_00", "SPEAKER_00"); // unchanged label: not sent
    await controller.submit();
    const [post] = posts(fetch.calls) as [Call];
    expect(header(post, "If-Match")).toBe('"4"');
    expect(header(post, "Idempotency-Key")).toMatch(/^[A-Za-z0-9_.-]{16,128}$/);
    expect(body(post)).toEqual({
      schema_version: 1, source_sha256: SHA, formats: ["srt", "json", "md"], show_speaker: true, srt_bom: true,
      newline: "crlf", speaker_labels: { Alice: "Bob" }, output_parent: null,
    });
    expect(controller.store.get().step).toBe("done");
    expect(controller.store.get().history.map(item => item.id)).toEqual([REX]);
  });

  it("drops the BOM when SRT is not selected and validates the form", async () => {
    const { controller } = setup(call => call.url.endsWith("/reexports") ? list() : json(200, source()));
    await controller.open(JOB);
    controller.edit({ formats: ["json"] });
    expect(controller.store.get().form?.srtBom).toBe(false);
    controller.edit({ formats: [] });
    expect(controller.issues()).toContain("formats");
    controller.edit({ formats: ["txt"], parent: "relative" });
    expect(controller.issues()).toEqual(["parent"]);
    controller.edit({ parent: "/abs" });
    controller.setLabel("Alice", "x".repeat(257));
    expect(controller.issues()).toEqual(["label"]);
    expect(controller.canSubmit()).toBe(false);
  });

  it("shows a refused source with its fixed code and offers no submission", async () => {
    const { fetch, controller } = setup(call => call.url.endsWith("/reexports") ? list() : json(200, source({ available: false, refusal: "source_json_not_recorded", source: null })));
    await controller.open(JOB);
    expect(controller.store.get().source?.refusal).toBe("source_json_not_recorded");
    expect(controller.canSubmit()).toBe(false);
    await controller.submit();
    expect(posts(fetch.calls)).toHaveLength(0);
  });

  it("unknown outcome: frozen, not closable, recheck resends the same key and body only", async () => {
    let attempt = 0;
    const { fetch, controller } = setup(call => {
      if (call.init.method !== "POST") return call.url.endsWith("/reexports") ? list() : json(200, source());
      attempt += 1;
      return attempt === 1 ? new TypeError("network down") : json(200, rec());
    });
    await controller.open(JOB);
    await controller.submit();
    expect(controller.store.get().step).toBe("unknown");
    controller.close();
    controller.edit({ formats: ["txt"] }); // ignored while unknown
    expect(controller.store.get().step).toBe("unknown");
    expect(controller.store.get().form?.formats).toEqual(["srt", "json", "md"]);
    await controller.submit(); // not a new intent while unknown
    expect(posts(fetch.calls)).toHaveLength(1);
    await controller.recheck();
    const [first, second] = posts(fetch.calls) as [Call, Call];
    expect(header(second, "Idempotency-Key")).toBe(header(first, "Idempotency-Key"));
    expect(second.init.body).toBe(first.init.body);
    expect(controller.store.get().step).toBe("done");
  });

  it("after a settled answer a new re-export uses a new key even with equal values (ABA)", async () => {
    const { fetch, controller } = setup(call => call.init.method === "POST" ? json(201, rec()) : call.url.endsWith("/reexports") ? list() : json(200, source()));
    await controller.open(JOB);
    await controller.submit();
    controller.again();
    await controller.submit();
    const [first, second] = posts(fetch.calls) as [Call, Call];
    expect(header(second, "Idempotency-Key")).not.toBe(header(first, "Idempotency-Key"));
    expect(second.init.body).toBe(first.init.body);
  });

  it("a 4xx refusal is shown with its code; editing drops the old intent", async () => {
    let attempt = 0;
    const { fetch, controller } = setup(call => {
      if (call.init.method !== "POST") return call.url.endsWith("/reexports") ? list() : json(200, source());
      attempt += 1;
      return attempt === 1
        ? json(409, { schema_version: 1, error: { code: "source_changed", message_key: "reexport.source_changed", request_id: "r", retryable: false, issues: [] } })
        : json(201, rec());
    });
    await controller.open(JOB);
    await controller.submit();
    expect(controller.store.get().step).toBe("rejected");
    expect(controller.store.get().error?.code).toBe("source_changed");
    controller.edit({ newline: "lf" });
    await controller.submit();
    const [first, second] = posts(fetch.calls) as [Call, Call];
    expect(header(second, "Idempotency-Key")).not.toBe(header(first, "Idempotency-Key"));
  });

  it("a late answer for a closed dialog is not applied", async () => {
    let release!: () => void;
    const { controller } = setup(call => call.url.endsWith("/reexports") ? list() : () => new Promise<Response>(resolve => { release = () => resolve(json(200, source())); }));
    const opening = controller.open(JOB);
    await Promise.resolve();
    controller.close();
    release();
    await opening;
    expect(controller.store.get().step).toBe("closed");
  });

  it("never puts labels or paths into the URL", async () => {
    const { fetch, controller } = setup(call => call.init.method === "POST" ? json(201, rec()) : call.url.endsWith("/reexports") ? list() : json(200, source()));
    await controller.open(JOB);
    controller.setLabel("Alice", "秘密の名前");
    controller.edit({ parent: "/secret/place" });
    await controller.submit();
    for (const call of fetch.calls) {
      expect(call.url).not.toContain("secret");
      expect(decodeURIComponent(call.url)).not.toContain("秘密");
    }
  });
});
