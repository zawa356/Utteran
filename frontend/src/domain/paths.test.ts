import { describe, expect, it } from "vitest";
import { checkPath, isAbsoluteLocalPath, MAX_JOB_PATH_LENGTH, samePath } from "./paths";

// Synthetic values assembled at runtime (no real user path).
const posix = ["", "synthetic", "media", "a.wav"].join("/");
const drive = ["D:", "synthetic", "a.wav"].join("\\");
const unc = ["", "", "server", "share", "a.wav"].join("\\");

describe("local path checks (no existence/permission claim)", () => {
  it("accepts drive, UNC and POSIX absolute paths", () => {
    for (const p of [posix, drive, unc, "D:/synthetic/a.wav"]) expect(checkPath(p, "input_file")).toBeNull();
    expect(isAbsoluteLocalPath("//host/x")).toBe(false);
  });

  it("refuses empty, relative, padded, control, overlong and folder-looking input", () => {
    expect(checkPath("", "input_file")).toBe("empty");
    expect(checkPath("a.wav", "input_file")).toBe("relative");
    expect(checkPath("D:a.wav", "input_file")).toBe("relative");
    expect(checkPath(`${posix} `, "input_file")).toBe("whitespace");
    expect(checkPath(`${posix}\n`, "input_file")).toBe("control");
    expect(checkPath(`/${"x".repeat(MAX_JOB_PATH_LENGTH)}`, "input_file")).toBe("too_long");
    expect(checkPath("/synthetic/media/", "input_file")).toBe("folder_like");
    expect(checkPath("/synthetic/media/", "output_folder")).toBeNull();
  });

  it("compares Windows-looking paths without case/separator differences, others exactly", () => {
    expect(samePath(drive, drive.toUpperCase().replace(/\\/g, "/"))).toBe(true);
    expect(samePath(posix, posix.toUpperCase())).toBe(false);
    expect(samePath(posix, posix)).toBe(true);
  });
});
