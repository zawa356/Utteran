import { describe, expect, it } from "vitest";
import { DemoController } from "./demo";
import { DIAGNOSTICS_EXCLUDED, jobLog, logFileName, maskForDiagnostics, setupLog, SYSTEM_LOG } from "./diagnostics";
import { exampleState } from "./setup";

describe("mock logs and diagnostics (no real data)", () => {
  it("masks paths and tokens (values assembled at runtime, never real)", () => {
    const win = "C:" + "\\" + ["Users", "demo" + "user", "rec", "a.wav"].join("\\");
    const nix = "/" + ["home", "demo" + "user", "rec", "b.m4a"].join("/");
    const token = "hf_" + "x".repeat(4) + "Y".repeat(8);
    const masked = maskForDiagnostics(`open ${win} and ${nix} with ${token}`);
    expect(masked).toBe("open <path> and <path> with <token>");
  });

  it("the sample logs carry no file names, paths or transcript text", () => {
    const demo = new DemoController();
    demo.setScenario("done");
    const job = demo.store.get().jobs[0]!;
    const lines = [...SYSTEM_LOG, ...jobLog(job)];
    const text = JSON.stringify(lines);
    expect(text).not.toContain(job.inputName);
    expect(text).not.toMatch(/[A-Za-z]:\\|\/home\/|\/Users\//);
    expect(lines.at(-1)?.key).toBe("log.job.done");
    expect(DIAGNOSTICS_EXCLUDED).toContain("diag.out.paths");
  });

  it("job log follows the job state", () => {
    const demo = new DemoController();
    demo.setScenario("failed");
    expect(jobLog(demo.store.get().jobs[0]!).at(-1)).toMatchObject({ level: "ERROR", key: "log.job.failed" });
  });

  it("log export targets: system / one job / setup, .log or .txt; the setup log follows the stages", () => {
    expect(logFileName("system", "log")).toBe("utteran-system-20261105.log");
    expect(logFileName("job", "txt", "demo-job-001")).toBe("utteran-demo-job-001-20261105.txt");
    const failed = setupLog(exampleState("failed"));
    expect(failed.at(-1)).toMatchObject({ level: "ERROR", key: "log.setup.stageFailed", params: { stage: "asr_model", code: "demo_download_failed" } });
    expect(setupLog(exampleState("incomplete")).at(-1)).toMatchObject({ level: "WARN", key: "log.setup.stageInterrupted" });
    expect(DIAGNOSTICS_EXCLUDED).toContain("diag.out.prompts");
  });
});
