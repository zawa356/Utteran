import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createV1ResourcesApi, parseDeletion, parseDeletionPlan } from "../../api/v1Resources";
import { deferred, json, scriptedFetch, type Call, type Reply } from "../../test/fakeFetch";
import { V1DeletionController } from "./jobDeletion";

const JOB = `job_${"a".repeat(32)}`;
const OTHER = `job_${"b".repeat(32)}`;
const TOKEN = `dp1.${"1".repeat(13)}.${"c".repeat(64)}`;
const NOW = "2026-11-17T00:00:00.000000Z";

function plan(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    job_id: JOB,
    mode: "history",
    allowed: true,
    refusal: null,
    plan_token: TOKEN,
    expires_at: NOW,
    resume: false,
    artifacts: [],
    related_jobs: 0,
    retained: ["backups", "replay_public_views", "event_skeleton", "core_cache"],
    ...overrides,
  };
}
function result(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    job_id: JOB,
    mode: "history",
    outcome: "completed",
    deleted_at: NOW,
    artifacts_removed: 0,
    artifacts_remaining: 0,
    retained: ["backups"],
    ...overrides,
  };
}

function setup(replies: Reply[] | ((call: Call) => Reply)) {
  const fetch = scriptedFetch(replies);
  const api = createV1ResourcesApi(createHttpClient({ fetch }));
  let settled = 0;
  const controller = new V1DeletionController(api, { onSettled: () => (settled += 1) });
  return { fetch, controller, settled: () => settled };
}
const target = (mode: "history" | "history_and_artifacts" = "history") => ({ rootId: JOB, jobId: JOB, revision: 7, mode });
const body = (call: Call) => JSON.parse(String(call.init.body)) as Record<string, unknown>;
const header = (call: Call, name: string) => new Headers(call.init.headers).get(name);

describe("C2c deletion parsers: allowlisted, consistent, value-free", () => {
  it("accepts a consistent plan and refuses unknown fields or inconsistent shapes", () => {
    expect(parseDeletionPlan(plan()).planToken).toBe(TOKEN);
    expect(() => parseDeletionPlan({ ...plan(), path: "/x" })).toThrow();
    expect(() => parseDeletionPlan(plan({ allowed: false }))).toThrow(); // allowed false with a token
    expect(() => parseDeletionPlan(plan({ refusal: "artifact_deletion_disabled" }))).toThrow();
    expect(() => parseDeletionPlan(plan({ artifacts: [{ format: "srt", size: 1 }] }))).toThrow(); // files in a history plan
    expect(() => parseDeletionPlan(plan({ plan_token: "v1.x" }))).toThrow();
    expect(() => parseDeletionPlan(plan({ refusal: "some_new_code", allowed: false, plan_token: null, expires_at: null }))).toThrow();
    expect(() => parseDeletionPlan({ ...plan(), mode: "history_and_artifacts", artifacts: [{ format: "srt", size: 1, name: "a.srt" }] })).toThrow();
    // Codes of the pre-review build are no longer part of the contract.
    for (const old of ["platform_unverified", "unexpected_entries", "artifact_changed"]) {
      expect(() => parseDeletionPlan(plan({ allowed: false, refusal: old, plan_token: null, expires_at: null }))).toThrow();
    }
    const refused = parseDeletionPlan(plan({ mode: "history_and_artifacts", allowed: false, refusal: "artifact_deletion_disabled", plan_token: null, expires_at: null }));
    expect(refused.allowed).toBe(false);
    expect(refused.refusal).toBe("artifact_deletion_disabled");
  });

  it("accepts completed/incomplete results and refuses contradictions", () => {
    expect(parseDeletion(result()).outcome).toBe("completed");
    expect(parseDeletion(result({ outcome: "incomplete", deleted_at: null, artifacts_remaining: 2 })).artifactsRemaining).toBe(2);
    expect(() => parseDeletion(result({ deleted_at: null }))).toThrow();
    expect(() => parseDeletion(result({ artifacts_remaining: 1 }))).toThrow();
    expect(() => parseDeletion({ ...result(), extra: true })).toThrow();
  });
});

describe("C2c deletion controller: plan → explicit approval → execute", () => {
  it("plans read-only, then sends the frozen plan once with If-Match and a key", async () => {
    const { fetch, controller, settled } = setup([json(200, plan()), json(200, result())]);
    await controller.plan(target());
    expect(controller.store.get().phase).toBe("planned");
    expect(fetch.calls).toHaveLength(1);
    expect(fetch.calls[0]!.url).toContain(`/api/v1/jobs/${JOB}/deletion-plans`);
    expect(header(fetch.calls[0]!, "Idempotency-Key")).toBeNull();
    expect(body(fetch.calls[0]!)).toEqual({ schema_version: 1, mode: "history" });
    await controller.confirm();
    expect(controller.store.get().phase).toBe("done");
    const sent = fetch.calls[1]!;
    expect(sent.url).toContain(`/api/v1/jobs/${JOB}/deletions`);
    expect(header(sent, "If-Match")).toBe('"7"');
    expect(header(sent, "Idempotency-Key")).toMatch(/.{16,}/);
    expect(body(sent)).toEqual({ schema_version: 1, mode: "history", plan_token: TOKEN });
    expect(settled()).toBe(1);
  });

  it("never offers approval for a refused plan (④ is disabled), and a second click is ignored while in flight", async () => {
    const refused = setup([json(200, plan({ mode: "history_and_artifacts", allowed: false, refusal: "artifact_deletion_disabled", plan_token: null, expires_at: null }))]);
    await refused.controller.plan(target("history_and_artifacts"));
    expect(refused.controller.canConfirm()).toBe(false);
    await refused.controller.confirm();
    expect(refused.fetch.calls).toHaveLength(1);

    const slow = deferred<Response>();
    const { fetch, controller } = setup([json(200, plan()), () => slow.promise]);
    await controller.plan(target());
    const first = controller.confirm();
    await controller.confirm();
    await controller.confirm();
    expect(fetch.calls).toHaveLength(2); // one plan, one execution
    controller.close(); // refused while executing
    expect(controller.store.get().phase).toBe("executing");
    slow.resolve(json(200, result()));
    await first;
    expect(controller.store.get().phase).toBe("done");
  });

  it("keeps the outcome categories apart: incomplete, rejected (stale), conflict (412)", async () => {
    // `incomplete` is not produced by the server while ④ is disabled; the client still keeps it apart.
    const partial = setup([json(200, plan()), json(200, result({ outcome: "incomplete", deleted_at: null, artifacts_removed: 0, artifacts_remaining: 1 }))]);
    await partial.controller.plan(target());
    await partial.controller.confirm();
    expect(partial.controller.store.get().phase).toBe("incomplete");

    const error = (status: number, code: string) => json(status, { schema_version: 1, error: { code, message_key: `jobs.${code}`, request_id: "r", retryable: false, issues: [] } });
    const stale = setup([json(200, plan()), error(409, "deletion_plan_stale")]);
    await stale.controller.plan(target());
    await stale.controller.confirm();
    expect(stale.controller.store.get().phase).toBe("rejected");
    expect(stale.controller.store.get().error?.code).toBe("deletion_plan_stale");
    expect(stale.fetch.calls).toHaveLength(2); // no automatic re-plan, no resend

    const conflict = setup([json(200, plan()), error(412, "revision_conflict")]);
    await conflict.controller.plan(target());
    await conflict.controller.confirm();
    expect(conflict.controller.store.get().phase).toBe("conflict");
  });

  it("an unknown outcome is re-checked only explicitly, with the same key and body", async () => {
    const { fetch, controller, settled } = setup([json(200, plan()), new TypeError("network down"), json(200, result())]);
    await controller.plan(target());
    await controller.confirm();
    expect(controller.store.get().phase).toBe("unknown");
    expect(settled()).toBe(0);
    expect(controller.busy()).toBe(true);
    await controller.plan({ ...target(), jobId: OTHER }); // refused while the outcome is unknown
    expect(fetch.calls).toHaveLength(2);
    await controller.recheck();
    expect(controller.store.get().phase).toBe("done");
    const [, first, second] = fetch.calls;
    expect(header(second!, "Idempotency-Key")).toBe(header(first!, "Idempotency-Key"));
    expect(second!.init.body).toBe(first!.init.body);
    expect(settled()).toBe(1);
  });

  it("refuses an answer about another job or another mode as malformed (unknown)", async () => {
    const { controller } = setup([json(200, plan()), json(200, result({ job_id: OTHER }))]);
    await controller.plan(target());
    await controller.confirm();
    expect(controller.store.get().phase).toBe("unknown");
    const wrongMode = setup([json(200, plan({ mode: "history" }))]);
    await wrongMode.controller.plan(target("history_and_artifacts"));
    expect(wrongMode.controller.store.get().phase).toBe("plan_error");
  });
});
