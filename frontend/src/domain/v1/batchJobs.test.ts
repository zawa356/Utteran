import { describe, expect, it } from "vitest";
import type { FetchLike } from "../../api/http";
import { createFakeHost } from "../../host/fake";
import { json } from "../../test/fakeFetch";
import { fakeV1Server, V1_POLICY, V1_PRESET } from "../../test/v1Server";
import { createCore } from "../core";
import { groupLogicalJobs } from "./logicalJobs";
import { V1_MAX_BATCH } from "./batchJobs";

const OUT = ["", "synthetic", "out"].join("/");
const media = (name: string) => ["", "synthetic", "media", name].join("/");

async function ready(names: string[] = ["a.wav", "b.wav", "c.wav"]) {
  const v1 = fakeV1Server();
  const lines: string[] = [];
  const fetch: FetchLike = async (url, init) => {
    const path = new URL(url, "http://x").pathname;
    lines.push(`${init.method} ${path}`);
    if (path.startsWith("/api/v1/")) return v1.fetch(url, init);
    if (path === "/api/settings") return json(200, { theme: "system", language: "ja", default_profile: null, default_output_dir: OUT });
    if (path === "/api/environment") return json(503, { detail: "synthetic" });
    throw new Error(`unexpected ${init.method} ${url}`);
  };
  const host = createFakeHost();
  const core = createCore({ fetch, host, apiMode: "v1-dev" });
  await core.start();
  await core.v1!.library.select("preset", V1_PRESET);
  await core.v1!.library.select("policy", V1_POLICY);
  for (const name of names) {
    host.nextPick = media(name);
    await core.inputs.pick(host);
  }
  core.inputs.selectAll();
  return { core, v1, lines, host, batch: core.v1!.batch, rows: () => core.inputs.store.get().rows };
}

const writes = (v1: ReturnType<typeof fakeV1Server>, path: string) => v1.writes().filter((w) => w.line === `POST ${path}`);
const legacyWrites = (lines: string[]) => lines.filter((l) => /^(POST|PATCH|PUT|DELETE) \/api\/(?!v1\/)/.test(l));

describe("S5-B v1 atomic batch (selected rows -> one B7 batch)", () => {
  it("freezes one session with a file per selected row, in list order, and submits it once", async () => {
    const { batch, v1, lines, rows } = await ready();
    expect(batch.evaluate()).toMatchObject({ issues: [], count: 3 });
    await batch.prepare();
    expect(batch.store.get().step).toBe("resolved");
    expect(batch.canSubmit()).toBe(true);
    await batch.submit();
    const state = batch.store.get();
    expect(state.step).toBe("accepted");
    expect(state.jobs).toHaveLength(3);
    const session = JSON.parse(writes(v1, "/api/v1/validations")[0]!.body!) as { files: { client_file_id: string; input_path: string; output_dir: string; overrides: unknown[] }[] };
    expect(session.files.map((f) => f.input_path)).toEqual(rows().map((r) => r.inputPath));
    expect(session.files.every((f) => f.output_dir === OUT && f.overrides.length === 0)).toBe(true);
    expect(new Set(session.files.map((f) => f.client_file_id)).size).toBe(3);
    // One validation, one resolution, one submission: the batch is a single B7 request.
    expect(writes(v1, "/api/v1/validations")).toHaveLength(1);
    expect(writes(v1, "/api/v1/execution-resolutions")).toHaveLength(1);
    expect(writes(v1, "/api/v1/jobs")).toHaveLength(1);
    const submitted = JSON.parse(writes(v1, "/api/v1/jobs")[0]!.body!) as { editing_session: unknown; resume_mode: string };
    expect(submitted.editing_session).toEqual(session);
    expect(submitted.resume_mode).toBe("resume");
    expect(state.jobs.map((j) => j.clientFileId)).toEqual(session.files.map((f) => f.client_file_id));
    expect(legacyWrites(lines)).toEqual([]);
  });

  it("only selected rows are sent; a file's own overrides and folder travel as its leaf overrides", async () => {
    const { core, batch, v1, rows } = await ready();
    const [a, b, c] = rows();
    core.inputs.clearSelection();
    core.inputs.toggle(b!.clientFileId, true);
    core.inputs.stage({ language: "en" });
    core.inputs.stageFormat("vtt", true);
    core.inputs.stage({ output: { kind: "custom", path: ["", "synthetic", "other"].join("/") } });
    expect(core.inputs.applyStaged()).toBe(1);
    core.inputs.toggle(c!.clientFileId, true);
    expect(batch.evaluate().count).toBe(2);
    await batch.prepare();
    expect(batch.store.get()).toMatchObject({ step: "resolved", mismatched: [] });
    const session = JSON.parse(writes(v1, "/api/v1/validations")[0]!.body!) as { files: { input_path: string; output_dir: string; overrides: { path: string; value: unknown }[] }[] };
    expect(session.files.map((f) => f.input_path)).toEqual([b!.inputPath, c!.inputPath]);
    expect(session.files[0]!.output_dir).toBe(["", "synthetic", "other"].join("/"));
    expect(session.files[0]!.overrides.map((o) => o.path).sort()).toEqual(["/asr/language", "/output/formats"]);
    expect(session.files[1]).toMatchObject({ output_dir: OUT, overrides: [] });
    expect(a!.selected).toBe(true); // the snapshot before clearSelection; `a` is simply not sent
    // What the screen computed per file is exactly what B5 validated.
    const prepared = batch.store.get().prepared!;
    expect((prepared.files[0]!.effective.asr as { language: string }).language).toBe("en");
    expect((prepared.files[1]!.effective.asr as { language: string }).language).not.toBe("en");
  });

  it("refuses before any request: nothing selected, too many, a bad path, a per-file device", async () => {
    const { core, batch, v1, rows } = await ready(["a.wav"]);
    core.inputs.clearSelection();
    expect(batch.evaluate().issues).toEqual([{ field: "rows", problem: "none" }]);
    core.inputs.selectAll();
    core.inputs.stage({ asrDevice: "cuda" });
    core.inputs.applyStaged();
    expect(batch.evaluate().issues).toEqual([{ field: "device", rowId: rows()[0]!.clientFileId, problem: "unsupported" }]);
    expect(batch.canPrepare()).toBe(false);
    await batch.prepare();
    expect(batch.store.get().step).toBe("idle");
    expect(v1.writes()).toEqual([]);
    expect(V1_MAX_BATCH).toBe(100);
  });

  it("any edit after prepare makes the resolution stale and blocks the submit", async () => {
    const { core, batch, v1, rows } = await ready();
    await batch.prepare();
    expect(batch.canSubmit()).toBe(true);
    core.inputs.toggle(rows()[2]!.clientFileId, false);
    expect(batch.isStale()).toBe(true);
    expect(batch.canSubmit()).toBe(false);
    await batch.submit();
    expect(writes(v1, "/api/v1/jobs")).toEqual([]);
    core.inputs.toggle(rows()[2]!.clientFileId, true);
    expect(batch.isStale()).toBe(false);
  });

  it("a validation that differs for one file stops everything and names that row", async () => {
    const { batch, v1, rows } = await ready();
    const evaluation = batch.evaluate();
    const other = { ...(evaluation.files[1]!.effective as Record<string, unknown>), asr: { ...(evaluation.files[1]!.effective.asr as object), language: "fr" } };
    v1.inject.set("POST /api/v1/validations", {
      reply: json(200, {
        schema_version: 1,
        valid: true,
        preset_ref: (evaluation.session as { preset_ref: unknown }).preset_ref,
        policy_ref: (evaluation.session as { policy_ref: unknown }).policy_ref,
        files: evaluation.files.map((f, i) => ({ client_file_id: f.fileId, valid: true, effective_settings: i === 1 ? other : f.effective, provenance: [], issues: [], warnings: [], worker_support: "unverified" })),
      }),
    });
    await batch.prepare();
    expect(batch.store.get()).toMatchObject({ step: "rejected", failedAt: "validation", mismatched: [rows()[1]!.clientFileId] });
    expect(writes(v1, "/api/v1/execution-resolutions")).toEqual([]);
    expect(writes(v1, "/api/v1/jobs")).toEqual([]);
  });

  it("a 4xx is a refusal with no job; a lost answer is unknown and re-checked with the same key and body only", async () => {
    const { batch, v1 } = await ready();
    v1.resolveError = { status: 409, code: "execution_unavailable", issues: [{ path: "/files/1", code: "no_runnable_candidate" }] };
    await batch.prepare();
    expect(batch.store.get()).toMatchObject({ step: "rejected", failedAt: "resolution" });
    expect(v1.jobs).toHaveLength(0);
    batch.reset();
    await batch.prepare();
    // The server applies the submission but the answer is lost.
    v1.inject.set("POST /api/v1/jobs", { reply: new TypeError("network"), apply: true });
    await batch.submit();
    expect(batch.store.get()).toMatchObject({ step: "unknown", failedAt: "submission" });
    expect(batch.canPrepare()).toBe(false); // nothing new is sent while the outcome is unknown
    const first = writes(v1, "/api/v1/jobs")[0]!;
    await batch.recheck();
    const second = writes(v1, "/api/v1/jobs")[1]!;
    expect(second.key).toBe(first.key);
    expect(second.body).toBe(first.body);
    expect(batch.store.get().step).toBe("accepted");
    // The replay returned the original jobs: three jobs exist, not six.
    expect(v1.jobs).toHaveLength(3);
    expect(batch.store.get().jobs.map((j) => j.id)).toEqual(v1.jobs.map((j) => j.id));
  });

  it("an answer that is not exactly one job per file is not treated as accepted", async () => {
    const { batch, v1 } = await ready();
    await batch.prepare();
    const one = fakeV1Server().addJobs(1);
    expect(one).toHaveLength(1);
    v1.inject.set("POST /api/v1/jobs", { reply: json(202, { schema_version: 1, jobs: [], queue_revision: 2 }) });
    await batch.submit();
    expect(batch.store.get().step).toBe("unknown");
    expect(batch.store.get().jobs).toEqual([]);
  });
});

describe("rerun", () => {
  it("keeps the target and shared gate across a lost response, then replays exactly once", async () => {
    const { core, v1, rows } = await ready(["a.wav"]);
    const { api, rerun, board, queueGate } = core.v1!;
    v1.addJobs(1);
    const [wire] = v1.jobs;
    wire!.status = "succeeded";
    const source = await api.getJob(wire!.id as string);
    const [group] = groupLogicalJobs([source]);
    rerun.openRerun(group!);
    expect(rerun.canPrepare()).toBe(false);
    const originalRows = rows();
    rerun.selectRerunInput(media("moved.wav"));
    await rerun.prepare();
    expect(rerun.store.get().prepared?.files).toHaveLength(1);
    expect(rows()).toBe(originalRows);
    const path = `/api/v1/jobs/${source.id}/rerun`;
    v1.inject.set(`POST ${path}`, { reply: new TypeError("network"), apply: true });
    await Promise.all([rerun.submit(), rerun.submit()]);
    expect(rerun.store.get().step).toBe("unknown");
    await board.refresh();
    rerun.closeRerun();
    rerun.selectRerunInput(media("ignored.wav"));
    expect(queueGate.busy()).toBe(true);
    expect(rerun.rerunSelection.get()).toEqual({ jobId: source.id, revision: source.revision, path: media("moved.wav") });
    await rerun.recheck();
    expect(writes(v1, path)).toHaveLength(2);
    expect(writes(v1, path)[0]).toEqual(writes(v1, path)[1]);
    expect(v1.jobs).toHaveLength(2);
    expect(rerun.store.get()).toMatchObject({ step: "accepted", jobs: [{ rerunOf: source.id, retryOf: null }] });
    expect(queueGate.busy()).toBe(false);
    expect(await api.getJob(source.id)).toEqual(source);
    core.dispose();
  });

  it("refuses to prepare the same target again after an obsolete source revision", async () => {
    const { core, v1 } = await ready();
    const { api, rerun, queueGate } = core.v1!;
    v1.addJobs(1);
    const [wire] = v1.jobs;
    wire!.status = "cancelled";
    const source = await api.getJob(wire!.id as string);
    rerun.openRerun(groupLogicalJobs([source])[0]!);
    rerun.selectRerunInput(media("a.wav"));
    await rerun.prepare();
    wire!.revision = source.revision + 1;
    await rerun.submit();
    expect(rerun.store.get()).toMatchObject({ step: "rejected", error: { status: 412 } });
    expect(rerun.canPrepare()).toBe(false);
    expect(queueGate.busy()).toBe(false);
    expect(v1.jobs).toHaveLength(1);
    core.dispose();
  });
});
