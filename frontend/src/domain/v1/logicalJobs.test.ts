import { describe, expect, it } from "vitest";
import { createHttpClient } from "../../api/http";
import { createV1ResourcesApi, parseArtifacts, parseJob, parseVisibility, type JobV1 } from "../../api/v1Resources";
import { json, scriptedFetch } from "../../test/fakeFetch";
import { SPEC, V1_POLICY, V1_PRESET } from "../../test/v1Server";
import { groupLogicalJobs, logicalJobOf } from "./logicalJobs";

const jid = (n: number): string => "job_" + n.toString(16).padStart(32, "0");

function wire(n: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    id: jid(n),
    revision: 1,
    status: "failed",
    held: false,
    blockers: [],
    block_reason: null,
    recovery_required: null,
    reason_code: "classified_failure",
    snapshot: {
      created_at: "2026-11-16T00:00:00Z",
      client_file_id: "f-0123456789abcdef",
      preset_ref: { id: V1_PRESET, revision: 1 },
      policy_ref: { id: V1_POLICY, revision: 1 },
      resume_mode: "resume",
      input_identity: "in_" + "0".repeat(32),
      spec: SPEC,
    },
    attempts: [],
    retry_of: null,
    rerun_of: null,
    result: null,
    error: null,
    created_at: "2026-11-16T00:00:00Z",
    updated_at: "2026-11-16T00:00:00Z",
    ...extra,
  };
}
const job = (n: number, extra: Record<string, unknown> = {}): JobV1 => parseJob(wire(n, extra));

describe("job links are parsed strictly", () => {
  it("reads retry_of / rerun_of and refuses anything that is not a job id or null", () => {
    expect(job(1)).toMatchObject({ retryOf: null, rerunOf: null });
    expect(job(2, { retry_of: jid(1) }).retryOf).toBe(jid(1));
    expect(job(3, { rerun_of: jid(1) }).rerunOf).toBe(jid(1));
    for (const bad of ["job_1", 7, "", undefined]) {
      expect(() => parseJob(wire(4, { retry_of: bad }))).toThrow();
      expect(() => parseJob(wire(4, { rerun_of: bad }))).toThrow();
    }
  });
});

describe("logical jobs (Design Freeze §7): one row per retry_of chain", () => {
  it("a retry chain is one logical job whose shown state is the newest internal job", () => {
    const jobs = [job(1), job(2, { retry_of: jid(1) }), job(3), job(4, { retry_of: jid(2), status: "succeeded" })];
    const groups = groupLogicalJobs(jobs);
    expect(groups.map((g) => g.rootId)).toEqual([jid(1), jid(3)]);
    expect(groups[0]!.jobIds).toEqual([jid(1), jid(2), jid(4)]);
    expect(groups[0]!.latest.id).toBe(jid(4));
    expect(groups[0]!.latest.status).toBe("succeeded");
    expect(groups[0]!.incomplete).toBe(false);
    expect(groups[1]!.attempts).toHaveLength(1);
    expect(logicalJobOf(groups, jid(2))?.rootId).toBe(jid(1));
    expect(logicalJobOf(groups, jid(9))).toBeNull();
    // Every internal job appears in exactly one logical job.
    expect(groups.flatMap((g) => g.jobIds).sort()).toEqual(jobs.map((j) => j.id).sort());
  });

  it("a rerun is a different logical job and only points at its origin", () => {
    const groups = groupLogicalJobs([job(1), job(2, { retry_of: jid(1) }), job(3, { rerun_of: jid(2) }), job(4, { rerun_of: jid(8) })]);
    expect(groups.map((g) => g.rootId)).toEqual([jid(1), jid(3), jid(4)]);
    expect(groups[1]!.rerunOfRootId).toBe(jid(1)); // the origin's logical job, not the internal id
    expect(groups[2]!.rerunOfRootId).toBe(jid(8)); // origin not present: raw id kept
    expect(groups[0]!.rerunOfRootId).toBeNull();
  });

  it("two retries of the same job stay in one logical job, in list order", () => {
    const groups = groupLogicalJobs([job(1), job(2, { retry_of: jid(1) }), job(3, { retry_of: jid(1) })]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.jobIds).toEqual([jid(1), jid(2), jid(3)]);
    expect(groups[0]!.latest.id).toBe(jid(3));
  });

  it("a retry whose source is not listed (hidden, other filter) gets its own row, marked incomplete", () => {
    const groups = groupLogicalJobs([job(2, { retry_of: jid(1) }), job(3, { retry_of: jid(2) })]);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ rootId: jid(2), incomplete: true });
    expect(groups[0]!.jobIds).toEqual([jid(2), jid(3)]);
  });

  it("never loops or loses a job on a cycle, a self link or a duplicate id", () => {
    const cyclic = [job(1, { retry_of: jid(2) }), job(2, { retry_of: jid(1) }), job(3, { retry_of: jid(3) }), job(1, { status: "succeeded" })];
    const groups = groupLogicalJobs(cyclic);
    expect(groups.flatMap((g) => g.jobIds).sort()).toEqual([jid(1), jid(2), jid(3)]);
    expect(groups.find((g) => g.jobIds.includes(jid(1)))!.attempts.find((j) => j.id === jid(1))!.status).toBe("failed");
    expect(groupLogicalJobs([])).toEqual([]);
  });

  it("keeps the root first even when the server listed a retry before it", () => {
    const groups = groupLogicalJobs([job(2, { retry_of: jid(1) }), job(1)]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.jobIds[0]).toBe(jid(1));
    expect(groups[0]!.incomplete).toBe(false);
  });
});

describe("history visibility and artifact view clients", () => {
  const visible = { schema_version: 1, job_id: jid(1), hidden: false, revision: 0, updated_at: null };
  const hidden = { schema_version: 1, job_id: jid(1), hidden: true, revision: 1, updated_at: "2026-11-16T01:00:00Z" };

  it("parsers accept only the contract shapes", () => {
    expect(parseVisibility(visible)).toEqual({ jobId: jid(1), hidden: false, revision: 0, updatedAt: null });
    expect(() => parseVisibility({ ...visible, revision: -1 })).toThrow();
    expect(() => parseVisibility({ ...visible, hidden: "no" })).toThrow();
    const view = parseArtifacts({ schema_version: 1, job_id: jid(1), state: "available", items: [{ format: "srt", size: 10, integrity: "changed" }], unexpected_count: 2 });
    expect(view).toEqual({ jobId: jid(1), state: "available", items: [{ format: "srt", size: 10, integrity: "changed" }], unexpectedCount: 2 });
    expect(() => parseArtifacts({ schema_version: 1, job_id: jid(1), state: "deleted", items: [], unexpected_count: 0 })).toThrow();
    expect(() => parseArtifacts({ schema_version: 1, job_id: jid(1), state: "available", items: [{ format: "exe", size: 1, integrity: "verified" }], unexpected_count: 0 })).toThrow();
  });

  it("PUT sends If-Match with the visibility revision, no Idempotency-Key, and checks the answer", async () => {
    const fetch = scriptedFetch([json(200, hidden, { ETag: '"1"' }), json(200, visible, { ETag: '"0"' }), json(200, { ...hidden, job_id: jid(2) })]);
    const api = createV1ResourcesApi(createHttpClient({ fetch }));
    expect(await api.setVisibility(jid(1), 0, true)).toMatchObject({ hidden: true, revision: 1 });
    const call = fetch.calls[0]!;
    const headers = new Headers(call.init.headers);
    expect(call.init.method).toBe("PUT");
    expect(call.url).toContain(`/api/v1/jobs/${jid(1)}/visibility`);
    expect(headers.get("If-Match")).toBe('"0"');
    expect(headers.get("Idempotency-Key")).toBeNull();
    expect(JSON.parse(call.init.body as string)).toEqual({ schema_version: 1, hidden: true });
    // An answer for the opposite state or another job is not accepted as success.
    await expect(api.setVisibility(jid(1), 0, true)).rejects.toMatchObject({ kind: "malformed" });
    await expect(api.setVisibility(jid(1), 0, true)).rejects.toMatchObject({ kind: "malformed" });
    await expect(api.setVisibility("job_x", 0, true)).rejects.toThrow();
  });

  it("the list filter travels with every page; artifacts are a plain GET", async () => {
    const fetch = scriptedFetch([json(200, { schema_version: 1, revision: 3, items: [], next_cursor: null }), json(200, { schema_version: 1, job_id: jid(1), state: "none", items: [], unexpected_count: 0 })]);
    const api = createV1ResourcesApi(createHttpClient({ fetch }));
    await api.listJobs("j2.cursor", undefined, "hidden");
    expect(fetch.calls[0]!.url).toContain("visibility=hidden");
    expect(fetch.calls[0]!.url).toContain("cursor=j2.cursor");
    expect((await api.getArtifacts(jid(1))).state).toBe("none");
    expect(fetch.calls[1]!.init.method).toBe("GET");
    expect(fetch.calls[1]!.url).toContain(`/api/v1/jobs/${jid(1)}/artifacts`);
  });
});
