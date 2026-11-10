import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import { type TranscriptionRequest, transcriptionBody } from "../api/legacyJobs";
import { accepted, controlledJobs, jobId, media, OUT, settle } from "../test/admissionHarness";
import { AdmissionGate, classifyStartFailure } from "./admission";
import { JobController } from "./job";

const request = (name = "a.wav"): TranscriptionRequest => ({
  inputPath: media(name),
  outputDir: OUT,
  profile: "cpu",
  asrBackend: "faster-whisper",
  asrModel: "small",
  asrDevice: "cpu",
  language: "ja",
  formats: ["srt"],
});

describe("AdmissionGate: the one door to POST /api/jobs", () => {
  it("single-flight: a second POST is refused while one is in flight, whoever asks", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const first = gate.admit(request("a.wav"), { origin: "batch" });
    expect(await gate.admit(request("b.wav"), { origin: "single" })).toEqual({ kind: "blocked", reason: "busy" });
    expect(gate.acquireLease()).toBeNull();
    expect(jobs.posts).toHaveLength(1);
    jobs.acceptAt(0);
    expect((await first).kind).toBe("accepted");
    const second = gate.admit(request("b.wav"), { origin: "single" });
    expect(jobs.posts).toHaveLength(2);
    jobs.acceptAt(1);
    expect((await second).kind).toBe("accepted");
    expect(jobs.maxInFlight()).toBe(1);
  });

  it("a batch lease keeps the single start out even between two batch POSTs", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const lease = gate.acquireLease()!;
    expect(gate.acquireLease()).toBeNull();
    expect(await gate.admit(request("x.wav"), { origin: "single" })).toEqual({ kind: "blocked", reason: "batch" });
    const sent = gate.admit(request("a.wav"), { origin: "batch", lease });
    jobs.acceptAt(0);
    await sent;
    expect(gate.blockFor(media("y.wav"))).toBe("batch");
    gate.releaseLease(lease);
    expect(gate.blockFor(media("y.wav"))).toBeNull();
    expect(jobs.posts).toHaveLength(1);
  });

  it("only a received 4xx is a refusal; no answer, an unreadable 2xx, a 5xx or a client error may have created a job", () => {
    for (const status of [400, 401, 403, 409, 422, 429]) expect(classifyStartFailure(new ApiError({ kind: "validation", status }))).toBe("rejected");
    expect(classifyStartFailure(new ApiError({ kind: "server", status: 500 }))).toBe("unknown");
    expect(classifyStartFailure(new ApiError({ kind: "unavailable", status: 503 }))).toBe("unknown");
    expect(classifyStartFailure(new ApiError({ kind: "malformed", status: 202 }))).toBe("unknown");
    expect(classifyStartFailure(new ApiError({ kind: "network", outcomeUnknown: true }))).toBe("unknown");
    expect(classifyStartFailure(new ApiError({ kind: "timeout", outcomeUnknown: true }))).toBe("unknown");
    expect(classifyStartFailure(new ApiError({ kind: "malformed", code: "client_error" }))).toBe("unknown");
  });

  it("guards accepted, unknown and sending inputs for the page lifetime; a refusal does not; Windows spellings fold", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const drive = (p: string) => ({ ...request(), inputPath: p });
    const sending = gate.admit(drive("C:\\media\\A.wav"), { origin: "single" });
    expect(gate.isGuarded("c:/media/a.wav")).toBe(true); // while sending
    jobs.loseAt(0);
    expect((await sending).kind).toBe("unknown");
    expect(gate.isGuarded("c:/MEDIA/a.wav")).toBe(true);
    const refused = gate.admit(request("r.wav"), { origin: "single" });
    jobs.refuseAt(1, 409);
    expect((await refused).kind).toBe("rejected");
    expect(gate.isGuarded(media("r.wav"))).toBe(false);
    expect(await gate.admit(drive("c:\\Media\\a.WAV"), { origin: "batch" })).toEqual({ kind: "blocked", reason: "same_input" });
    // POSIX paths compare exactly.
    expect(gate.isGuarded(media("R.wav"))).toBe(false);
    expect(jobs.posts).toHaveLength(2);
  });

  it("the sent request is a frozen copy: editing the caller's object never changes what was sent or recorded", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const mutable = request("frozen.wav");
    const sent = gate.admit(mutable, { clientFileId: "row-1", origin: "batch" });
    mutable.outputDir = `${OUT}/changed`;
    mutable.formats.push("vtt");
    jobs.acceptAt(0);
    const result = await sent;
    if (result.kind !== "accepted") throw new Error("expected accepted");
    expect(result.record).toMatchObject({ clientFileId: "row-1", origin: "batch", phase: "accepted", jobId: jobId(1) });
    expect(result.record.snapshot.request.outputDir).toBe(OUT);
    expect(result.record.snapshot.body).toEqual(transcriptionBody({ ...request("frozen.wav") }));
    expect(jobs.posts[0]!.body).toEqual(result.record.snapshot.body);
    expect(Object.isFrozen(result.record.snapshot.request.formats)).toBe(true);
    expect(gate.latestFor("row-1")?.seq).toBe(1);
    expect(gate.latestFor("row-1", 2)).toBeNull(); // attempts before a reset floor are ignored
  });

  it("JobController (S3-A) sends through the same gate: its POST blocks the door, a batch lease blocks it", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const job = new JobController(jobs.api, { gate });
    const lease = gate.acquireLease()!;
    expect(job.blockFor(request("s.wav"))).toBe("batch");
    expect(await job.submit(request("s.wav"))).toBe(false);
    gate.releaseLease(lease);
    const single = job.submit(request("s.wav"), { clientFileId: "row-s" });
    expect(job.store.get().snapshot?.request.inputPath).toBe(media("s.wav")); // shown while sending
    expect(gate.acquireLease()).toBeNull();
    jobs.posts[0]!.answer.resolve(accepted(jobId(7)));
    expect(await single).toBe(true);
    await settle();
    expect(gate.latestFor("row-s")).toMatchObject({ origin: "single", phase: "accepted", jobId: jobId(7) });
    expect(jobs.posts).toHaveLength(1);
    job.dispose();
  });

  it("a 5xx answer to the single start is 'unknown' (never a refusal that would allow a resend)", async () => {
    const jobs = controlledJobs();
    const gate = new AdmissionGate(jobs.api);
    const job = new JobController(jobs.api, { gate });
    const sent = job.submit(request("five.wav"));
    jobs.posts[0]!.answer.reject(new ApiError({ kind: "server", status: 500 }));
    expect(await sent).toBe(false);
    expect(job.store.get().submit.phase).toBe("unknown");
    expect(job.blockFor(request("other.wav"))).toBe("unknown_unresolved");
    job.dismiss();
    expect(job.blockFor(request("five.wav"))).toBe("same_input");
    job.dispose();
  });
});
