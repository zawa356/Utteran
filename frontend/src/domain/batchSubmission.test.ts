import { describe, expect, it } from "vitest";
import { b1Harness, jobId, media, OUT, settingsState, settle } from "../test/admissionHarness";
import { countItems } from "./batchSubmission";
import { JobController } from "./job";

/** Three selected rows a/b/c, common model chosen, saved default output. */
function three() {
  const h = b1Harness();
  h.form.choose("asrModel", "synthetic-a");
  h.inputs.acceptDrop(["a.wav", "b.wav", "c.wav"].map((n) => ({ path: media(n), kind: "file" as const })));
  const ids = h.inputs.store.get().rows.map((r) => r.clientFileId);
  const run = () => h.batch.store.get().run!;
  const phases = () => run().items.map((i) => i.phase);
  const status = () => h.inputs.store.get().rows.map((r) => h.inputs.statusOf(r));
  return { h, ids, run, phases, status };
}

describe("preflight: everything is checked before the first POST", () => {
  it("one invalid file → no POST at all, with a reason per file", async () => {
    const { h, ids } = three();
    h.inputs.clearSelection();
    h.inputs.toggle(ids[1]!, true);
    h.inputs.stage({ output: { kind: "custom", path: `${OUT}/b` } });
    h.inputs.applyStaged();
    h.inputs.selectAll();
    h.form.edit({ outputDir: "relative/out" }); // a and c inherit an unusable folder; b has its own
    expect(await h.batch.start()).toBe(false);
    expect(h.jobs.posts).toHaveLength(0);
    const preflight = h.batch.store.get().preflight!;
    expect(preflight.block).toBe("invalid");
    expect(preflight.failures.map((f) => [f.fileName, f.issues])).toEqual([
      ["a.wav", [{ field: "output", problem: "relative" }]],
      ["c.wav", [{ field: "output", problem: "relative" }]],
    ]);
    expect(h.batch.store.get().run).toBeNull();
  });

  it("nothing selected / nothing unsent → nothing is sent", async () => {
    const { h } = three();
    h.inputs.clearSelection();
    expect(await h.batch.start()).toBe(false);
    expect(h.batch.store.get().preflight?.block).toBe("nothing_selected");
    expect(h.jobs.posts).toHaveLength(0);
  });
});

describe("staged sending through the shared gate", () => {
  it("three accepted: three POSTs, never two at once, each body as frozen at the start", async () => {
    const { h, run, phases, status } = three();
    const started = h.batch.start();
    expect(phases()).toEqual(["pending", "waiting", "waiting"]);
    expect(h.jobs.posts).toHaveLength(1);
    // Edits while the batch runs never change the bodies still to be sent.
    h.form.edit({ language: "en", outputDir: `${OUT}/later` });
    h.form.toggleFormat("vtt", true);
    h.set.set(settingsState(`${OUT}/server-changed`)); // the server-side default moved (remember_directories)
    h.inputs.selectAll();
    h.inputs.stage({ language: "auto" });
    expect(h.inputs.applyStaged()).toBe(0); // queued rows of the running batch are locked (PR #43 P2-2)
    h.jobs.acceptAt(0);
    await settle();
    expect(h.jobs.posts).toHaveLength(2);
    h.jobs.acceptAt(1);
    await settle();
    h.jobs.acceptAt(2);
    expect(await started).toBe(true);
    expect(h.jobs.maxInFlight()).toBe(1);
    expect(h.jobs.posts.map((p) => p.body.input_path)).toEqual([media("a.wav"), media("b.wav"), media("c.wav")]);
    for (const post of h.jobs.posts) expect(post.body).toMatchObject({ output_dir: OUT, language: "ja", formats: ["srt", "json", "md"], asr_model: "synthetic-a", diarization_enabled: false, resume_mode: "resume", recursive: false });
    expect(run().items.map((i) => i.jobId)).toEqual([jobId(1), jobId(2), jobId(3)]);
    expect(run()).toMatchObject({ phase: "finished", stopReason: null });
    expect(status()).toEqual(["accepted", "accepted", "accepted"]);
    expect(h.afterRun).toEqual([{ accepted: 3, rejected: 0, unknown: 0, notSent: 0, pending: 0 }]);
    // A finished batch has nothing left to send (all accepted rows are excluded).
    expect(await h.batch.start()).toBe(false);
    expect(h.batch.store.get().preflight?.block).toBe("nothing_selected");
    expect(h.jobs.posts).toHaveLength(3);
  });

  it("the 2nd answer is a 400: 1 accepted, 1 refused, 1 not sent, no further POST; continuing sends only the unsent row", async () => {
    const { h, phases, run, status } = three();
    const started = h.batch.start();
    h.jobs.acceptAt(0);
    await settle();
    h.jobs.refuseAt(1, 400);
    expect(await started).toBe(false);
    expect(phases()).toEqual(["accepted", "rejected", "not_sent"]);
    expect(run()).toMatchObject({ phase: "stopped", stopReason: "rejected" });
    expect(countItems(run().items)).toMatchObject({ accepted: 1, rejected: 1, unknown: 0, notSent: 1 });
    await settle();
    expect(h.jobs.posts).toHaveLength(2);
    // The server detail (which may contain a path) is kept only inside the ApiError, never in a notice.
    expect(JSON.stringify(h.batch.store.get().preflight)).not.toContain("synthetic");
    expect(status()).toEqual(["accepted", "rejected", "not_submitted"]);
    const again = h.batch.start();
    expect(h.jobs.posts.map((p) => p.body.input_path)).toEqual([media("a.wav"), media("b.wav"), media("c.wav")]);
    h.jobs.acceptAt(2);
    expect(await again).toBe(true);
    expect(status()).toEqual(["accepted", "rejected", "accepted"]);
    expect(h.jobs.posts).toHaveLength(3); // the refused row was not resent automatically
  });

  it("the 2nd answer is lost: 'unknown', stop at once; a later start never resends accepted or unknown rows", async () => {
    const { h, phases, run, status } = three();
    const started = h.batch.start();
    h.jobs.acceptAt(0);
    await settle();
    h.jobs.loseAt(1);
    expect(await started).toBe(false);
    expect(phases()).toEqual(["accepted", "unknown", "not_sent"]);
    expect(run().stopReason).toBe("unknown");
    await settle();
    expect(h.jobs.posts).toHaveLength(2);
    expect(status()).toEqual(["accepted", "unknown", "not_submitted"]);
    const again = h.batch.start();
    expect(h.jobs.posts.at(-1)!.body.input_path).toBe(media("c.wav"));
    h.jobs.acceptAt(2);
    await again;
    expect(h.jobs.posts.map((p) => p.body.input_path)).toEqual([media("a.wav"), media("b.wav"), media("c.wav")]);
    // The unknown row stays locked and guarded.
    expect(h.inputs.evaluateRow(h.inputs.store.get().rows[1]!).sameInput).toBe(true);
  });

  it("stop during a POST never aborts it; after its answer nothing more is sent", async () => {
    const { h, phases, run } = three();
    const started = h.batch.start();
    h.batch.requestStop();
    expect(run().phase).toBe("stopping");
    expect(h.jobs.posts).toHaveLength(1);
    h.jobs.acceptAt(0); // the request in flight completes normally
    expect(await started).toBe(false);
    await settle();
    expect(h.jobs.posts).toHaveLength(1);
    expect(phases()).toEqual(["accepted", "not_sent", "not_sent"]);
    expect(run()).toMatchObject({ phase: "stopped", stopReason: "user" });
    expect(h.gate.store.get().lease).toBeNull();
  });

  it("dispose (page closed) during a POST: the answer is still recorded, nothing more is sent", async () => {
    const { h, status } = three();
    const started = h.batch.start();
    h.batch.dispose();
    h.jobs.acceptAt(0);
    await started;
    await settle();
    expect(h.jobs.posts).toHaveLength(1);
    expect(status()[0]).toBe("accepted");
    expect(h.afterRun).toEqual([]);
  });

  it("single start (S3-A) and batch never send at the same time or interleave", async () => {
    const { h, ids } = three();
    const job = new JobController(h.jobs.api, { gate: h.gate });
    const row = h.inputs.row(ids[0]!)!;
    // Single first: the batch is refused while it is in flight.
    const single = job.submit(h.inputs.evaluateRow(row).request!, { clientFileId: row.clientFileId });
    expect(await h.batch.start()).toBe(false);
    expect(h.batch.store.get().preflight?.block).toBe("busy");
    expect(h.jobs.posts).toHaveLength(1);
    h.jobs.acceptAt(0);
    expect(await single).toBe(true);
    // Batch next: the single start is refused for the whole run, even between POSTs.
    const started = h.batch.start();
    expect(h.jobs.posts.map((p) => p.body.input_path)).toEqual([media("a.wav"), media("b.wav")]);
    const other = h.inputs.row(ids[2]!)!;
    expect(job.blockFor(h.inputs.evaluateRow(other).request)).toBe("busy");
    expect(h.gate.blockFor(other.inputPath)).toBe("busy"); // b is in flight
    h.jobs.acceptAt(1);
    await settle();
    expect(h.gate.blockFor(null)).toBe("busy");
    h.jobs.acceptAt(2);
    await started;
    expect(h.jobs.maxInFlight()).toBe(1);
    expect(h.jobs.posts).toHaveLength(3);
    job.dispose();
  });

  it("A → B → A: an input sent by the single start is refused by the batch preflight (and Windows spellings match)", async () => {
    const h = b1Harness();
    h.form.choose("asrModel", "synthetic-a");
    const job = new JobController(h.jobs.api, { gate: h.gate });
    // A is sent without a list row (e.g. before it was added), then B, then A is added again.
    h.inputs.setTyped("C:\\media\\A.wav");
    h.inputs.addTyped();
    const a = h.inputs.store.get().rows[0]!;
    const sendA = job.submit(h.inputs.evaluateRow(a).request!, { clientFileId: null });
    h.jobs.acceptAt(0);
    await sendA;
    await settle();
    job.dismiss();
    h.inputs.setTyped(media("b.wav"));
    h.inputs.addTyped();
    const b = h.inputs.store.get().rows[1]!;
    h.inputs.clearSelection();
    h.inputs.toggle(b.clientFileId, true);
    const sendB = h.batch.start();
    h.jobs.acceptAt(1);
    await sendB;
    // The A row was never tied to the attempt (clientFileId null) yet stays guarded by path.
    h.inputs.clearSelection();
    h.inputs.toggle(a.clientFileId, true);
    expect(await h.batch.start()).toBe(false);
    expect(h.batch.store.get().preflight).toMatchObject({ block: "invalid", failures: [{ fileName: "A.wav", sameInput: true }] });
    expect(h.gate.isGuarded("c:/MEDIA/a.wav")).toBe(true);
    expect(h.jobs.posts).toHaveLength(2);
    job.dispose();
  });

  it("nothing is written to browser storage", async () => {
    const { h } = three();
    const started = h.batch.start();
    h.jobs.refuseAt(0);
    await started;
    expect(globalThis.localStorage?.length ?? 0).toBe(0);
    expect(globalThis.sessionStorage?.length ?? 0).toBe(0);
  });
});

describe("PR #43 P2-2: rows of a running batch stay in the list", () => {
  it("removing waiting rows while the first POST is pending keeps them (and their frozen sends) visible; stop sends none of them", async () => {
    const { h, ids, phases } = three();
    const started = h.batch.start();
    expect(h.jobs.posts).toHaveLength(1); // a is pending
    h.inputs.clearSelection();
    h.inputs.toggle(ids[1]!, true);
    h.inputs.toggle(ids[2]!, true);
    h.inputs.removeSelected();
    // Nothing that the batch will (or may) send disappears from the list.
    expect(h.inputs.store.get().rows.map((r) => r.clientFileId)).toEqual(ids);
    expect(h.inputs.store.get().notice).toEqual({ kind: "removed", removed: 0, kept: 2 });
    expect(h.inputs.store.get().rows.map((r) => h.inputs.statusOf(r))).toEqual(["pending", "queued", "queued"]);
    // The explicit way to keep b/c from being sent is Stop.
    h.batch.requestStop();
    h.jobs.acceptAt(0);
    await started;
    await settle();
    expect(h.jobs.posts).toHaveLength(1);
    expect(phases()).toEqual(["accepted", "not_sent", "not_sent"]);
    // After the run, never-sent rows are ordinary rows again and can be removed.
    expect(h.inputs.store.get().rows.map((r) => h.inputs.statusOf(r))).toEqual(["accepted", "not_submitted", "not_submitted"]);
    h.inputs.removeSelected();
    expect(h.inputs.store.get().rows.map((r) => r.clientFileId)).toEqual([ids[0]]);
  });

  it("without Stop, every row the list shows as waiting is exactly what is sent next (no hidden sends)", async () => {
    const { h, ids } = three();
    const started = h.batch.start();
    h.inputs.selectAll();
    h.inputs.removeSelected();
    h.jobs.acceptAt(0);
    await settle();
    h.jobs.acceptAt(1);
    await settle();
    h.jobs.acceptAt(2);
    await started;
    const shown = h.inputs.store.get().rows.map((r) => r.clientFileId);
    const sentFor = h.gate.store.get().records.map((r) => r.clientFileId);
    expect(sentFor).toEqual(ids);
    for (const id of sentFor) expect(shown).toContain(id);
  });
});
