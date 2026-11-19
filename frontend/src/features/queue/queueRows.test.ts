import { describe, expect, it } from "vitest";
import type { JobStatusV1, JobV1 } from "../../api/v1Resources";
import type { QueuePlace } from "../../domain/v1/jobBoard";
import { currentJob } from "../../domain/v1/jobBoard";
import { groupLogicalJobs } from "../../domain/v1/logicalJobs";
import { buildQueueRows, matchesFilter, normalizeQuery, placeOfRow, QUEUE_FILTERS, QUEUE_SORTS, type QueueRow, reconcileSelection, viewRows } from "./queueRows";

/**
 * Bundle D2-A: the queue page shows only what B7 answered. These pure rules decide the rows, the
 * search, the filters and the display sort; none of them may change B7's order or invent a place.
 */

const id = (n: number) => `job_${n.toString(16).padStart(32, "0")}`;
function job(n: number, status: JobStatusV1, extra: Partial<JobV1> = {}): JobV1 {
  return {
    id: id(n),
    revision: 1,
    status,
    held: false,
    blockers: [],
    blockReason: null,
    recoveryRequired: null,
    reasonCode: "submitted",
    clientFileId: `f-${n}`,
    presetRef: { id: "pre_" + "1".repeat(32), revision: 1 },
    policyRef: { id: "pol_" + "2".repeat(32), revision: 1 },
    spec: {
      candidateId: "fw-cpu.cpu.generation-1",
      recipeId: "fw-cpu",
      profileId: "cpu",
      modelId: "large-v3-turbo",
      modelRevision: "revision-1",
      backend: "faster-whisper",
      device: "cpu",
      computeType: "int8",
      policyRef: { id: "pol_" + "2".repeat(32), revision: 1 },
      reasonCodes: ["supported"],
      confidence: "medium",
      evidenceKinds: ["smoke"],
      warnings: [],
      fallbackCandidateIds: [],
      workerSupport: "supported",
    },
    attempts: 0,
    lastAttemptState: null,
    artifactCount: null,
    errorCode: null,
    retryOf: null,
    rerunOf: null,
    createdAt: "2026-11-19T00:00:00Z",
    updatedAt: "2026-11-19T00:00:00Z",
    ...extra,
  };
}

function rowsOf(jobs: JobV1[], places: Record<string, QueuePlace>, names: Record<string, string> = {}): QueueRow[] {
  // Like the board: unfinished rows in B7 order (active, then position; no place = last, stable).
  const rank = (place: QueuePlace | undefined) => (place === undefined ? Infinity : place.active ? 0 : (place.position ?? Infinity));
  const groups = groupLogicalJobs(jobs)
    .filter((g) => g.open.length > 0)
    .map((g, index) => ({ g, index }))
    .sort((a, b) => rank(places[currentJob(a.g, places).id]) - rank(places[currentJob(b.g, places).id]) || a.index - b.index)
    .map(({ g }) => g);
  return buildQueueRows(
    groups,
    (g) => currentJob(g, places),
    (jobId) => places[jobId] ?? null,
    (clientFileId) => names[clientFileId] ?? null,
  );
}
const active: QueuePlace = { active: true, position: null, held: false, blockers: [] };
const waiting = (position: number, held = false): QueuePlace => ({ active: false, position, held, blockers: [] });
const roots = (rows: readonly QueueRow[]) => rows.map((r) => r.rootId);
const DEFAULT = { query: "", filter: "all" as const, sort: "queue" as const };

describe("D2A queue rows", () => {
  it("D2A-02: a retried input is one row keyed by its root; the row shows the job B7 runs first", () => {
    const jobs = [job(1, "failed"), job(2, "queued", { retryOf: id(1) })];
    const rows = rowsOf(jobs, { [id(2)]: waiting(1) });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.rootId).toBe(id(1));
    expect(rows[0]!.current.id).toBe(id(2));
    expect(rows[0]!.group.attempts).toHaveLength(2);
  });

  it("D2A-02: a branched chain stays one row; the current job is the branch B7 runs first, not the newest", () => {
    const jobs = [job(1, "failed"), job(2, "queued", { retryOf: id(1) }), job(3, "running", { retryOf: id(1) })];
    const rows = rowsOf(jobs, { [id(2)]: waiting(1), [id(3)]: active });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.current.id).toBe(id(3));
    expect(rows[0]!.group.open.map((j) => j.id)).toEqual([id(2), id(3)]);
    expect(rows[0]!.group.branched).toBe(true);
  });

  it("D2A-03: the place is B7's own: active, waiting with B7's position, or unknown (never 0, never the screen index)", () => {
    const rows = rowsOf([job(1, "running"), job(2, "queued"), job(3, "waiting_user")], { [id(1)]: active, [id(2)]: waiting(7) });
    expect(rows.map(placeOfRow)).toEqual([{ kind: "active", position: null }, { kind: "waiting", position: 7 }, { kind: "unknown", position: null }]);
  });

  it("D2A-03: a queue item without a position (null) is unknown, not position 0", () => {
    const rows = rowsOf([job(1, "queued")], { [id(1)]: { active: false, position: null, held: false, blockers: [] } });
    expect(placeOfRow(rows[0]!)).toEqual({ kind: "unknown", position: null });
  });

  it("D2A-06: the name is only a locally known basename; otherwise null (the page then says the name is unknown)", () => {
    const rows = rowsOf([job(1, "queued"), job(2, "queued")], {}, { "f-1": "meeting.wav" });
    expect(rows.map((r) => r.name)).toEqual(["meeting.wav", null]);
  });
});

describe("D2A queue view: search, filter and display sort", () => {
  const jobs = [
    job(1, "running", { createdAt: "2026-11-19T00:00:03Z" }),
    job(2, "queued", { createdAt: "2026-11-19T00:00:01Z" }),
    job(3, "held", { held: true, createdAt: "2026-11-19T00:00:02Z" }),
    job(4, "waiting_user", { createdAt: "2026-11-19T00:00:01Z", spec: { ...job(0, "queued").spec, modelId: "kotoba-whisper-v2", profileId: "cuda" } }),
    job(5, "blocked", { blockReason: "profile_unavailable", createdAt: "2026-11-19T00:00:00Z" }),
  ];
  const places = { [id(1)]: active, [id(2)]: waiting(1), [id(3)]: waiting(2, true), [id(5)]: waiting(3) };
  const rows = () => rowsOf(jobs, places, { "f-2": "Zeta 会議.wav", "f-5": "alpha.wav" });

  it("D2A-05: the default is the board's B7 order; rows without a place come last", () => {
    const view = viewRows(rows(), DEFAULT);
    expect(roots(view)).toEqual(roots(rows()));
    expect(view.map((r) => r.queueIndex)).toEqual([0, 1, 2, 3, 4]);
  });

  it("D2A-05: search uses the known name, internal job ids, model and profile only (NFKC, case-insensitive)", () => {
    expect(roots(viewRows(rows(), { ...DEFAULT, query: "ｚｅｔａ" }))).toEqual([id(2)]);
    expect(roots(viewRows(rows(), { ...DEFAULT, query: id(3).slice(-6) }))).toEqual([id(3)]);
    expect(roots(viewRows(rows(), { ...DEFAULT, query: "KOTOBA" }))).toEqual([id(4)]);
    expect(roots(viewRows(rows(), { ...DEFAULT, query: "cuda" }))).toEqual([id(4)]);
    // No path is known, so nothing matches a directory name.
    expect(viewRows(rows(), { ...DEFAULT, query: "/in/" })).toEqual([]);
    // Whitespace only is no search.
    expect(viewRows(rows(), { ...DEFAULT, query: "   " })).toHaveLength(5);
    // A retry's id finds its logical row.
    const chain = rowsOf([job(8, "failed"), job(9, "queued", { retryOf: id(8) })], {});
    expect(roots(viewRows(chain, { ...DEFAULT, query: id(9) }))).toEqual([id(8)]);
  });

  it("D2A-05: status filters use only values the DTO distinguishes", () => {
    expect(QUEUE_FILTERS).toEqual(["all", "running", "waiting", "held", "attention"]);
    const by = (filter: (typeof QUEUE_FILTERS)[number]) => roots(rows().filter((r) => matchesFilter(r, filter)));
    expect(by("running")).toEqual([id(1)]);
    expect(by("waiting")).toEqual([id(2)]);
    expect(by("held")).toEqual([id(3)]);
    expect(by("attention")).toEqual([id(5), id(4)]);
  });

  it("D2A-05: a held queue item counts as held even when the job status says queued", () => {
    const held = rowsOf([job(1, "queued")], { [id(1)]: waiting(1, true) });
    expect(matchesFilter(held[0]!, "held")).toBe(true);
    expect(matchesFilter(held[0]!, "waiting")).toBe(false);
  });

  it("D2A-05: display sorts are stable, fall back to B7 order, and never renumber or drop a row", () => {
    expect(QUEUE_SORTS).toEqual(["queue", "created", "status", "name"]);
    const created = viewRows(rows(), { ...DEFAULT, sort: "created" });
    expect(roots(created)).toEqual([id(5), id(2), id(4), id(3), id(1)]);
    const named = viewRows(rows(), { ...DEFAULT, sort: "name" });
    // Known names first (locale order), unknown names last in B7 order.
    expect(roots(named)).toEqual([id(5), id(2), id(1), id(3), id(4)]);
    const status = viewRows(rows(), { ...DEFAULT, sort: "status" });
    expect(roots(status)[0]).toBe(id(1));
    for (const view of [created, named, status]) {
      expect([...roots(view)].sort()).toEqual([...roots(rows())].sort());
      // queueIndex is B7's order from the board, unchanged by any display sort.
      expect(Object.fromEntries(view.map((r) => [r.rootId, r.queueIndex]))).toEqual(Object.fromEntries(rows().map((r) => [r.rootId, r.queueIndex])));
    }
  });

  it("D2A-05: the view never mutates the board's rows", () => {
    const source = rows();
    const before = roots(source);
    viewRows(source, { ...DEFAULT, sort: "name" });
    viewRows(source, { ...DEFAULT, sort: "created", filter: "waiting", query: "a" });
    expect(roots(source)).toEqual(before);
  });

  it("normalizeQuery folds width and case and trims", () => {
    expect(normalizeQuery("  ＡＢｃ  ")).toBe("abc");
  });
});

describe("D2A selection", () => {
  const rows = rowsOf([job(1, "running"), job(2, "queued")], { [id(1)]: active, [id(2)]: waiting(1) });

  it("D2A-04: a selected row that is still in the queue stays selected", () => {
    expect(reconcileSelection(id(2), "done", rows)).toEqual({ selected: id(2), lost: false });
  });

  it("D2A-04: a selected row that left the queue is deselected explicitly", () => {
    expect(reconcileSelection(id(3), "done", rows)).toEqual({ selected: null, lost: true });
  });

  it("D2A-07: while the board is loading or failed, nothing is decided from the (empty) rows", () => {
    expect(reconcileSelection(id(2), "error", [])).toEqual({ selected: id(2), lost: false });
    expect(reconcileSelection(id(2), "pending", [])).toEqual({ selected: id(2), lost: false });
    expect(reconcileSelection(null, "done", rows)).toEqual({ selected: null, lost: false });
  });
});
