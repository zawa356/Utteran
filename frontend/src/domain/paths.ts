/**
 * Client-side checks for the local paths S3-A sends (input file, output folder).
 *
 * The paths name files on the machine that runs the Utteran server (the same PC), not uploads.
 * These checks only refuse values that are certainly unusable or ambiguous; they never prove
 * that a file exists or that a folder is writable — the server and the CLI decide that.
 * Nothing is rewritten: no trimming, no joining, no "same folder as the input" composition.
 */

/** `TranscriptionPayload.input_path` / `output_dir` limit (api.py). */
export const MAX_JOB_PATH_LENGTH = 32_768;

export type PathProblem = "empty" | "too_long" | "control" | "whitespace" | "relative" | "folder_like";

// C0/C1 control characters and bidi overrides (same set as legacyEnvironment.safeText).
const UNSAFE = /[\u0000-\u001f\u007f-\u009f‪-‮⁦-⁩]/;
const WINDOWS_DRIVE = /^[A-Za-z]:[\\/]/;
const UNC = /^\\\\[^\\/]+[\\/][^\\/]+/;

/** Drive (`C:\`), UNC (`\\server\share`) or POSIX (`/…`). A relative path would be resolved by the server process. */
export function isAbsoluteLocalPath(value: string): boolean {
  return WINDOWS_DRIVE.test(value) || UNC.test(value) || (value.startsWith("/") && !value.startsWith("//"));
}

function windowsLike(value: string): boolean {
  return WINDOWS_DRIVE.test(value) || UNC.test(value);
}

export function checkPath(value: string, kind: "input_file" | "output_folder"): PathProblem | null {
  if (value === "") return "empty";
  if (value.length > MAX_JOB_PATH_LENGTH) return "too_long";
  if (UNSAFE.test(value)) return "control";
  if (value.trim() !== value) return "whitespace";
  if (!isAbsoluteLocalPath(value)) return "relative";
  if (kind === "input_file" && /[\\/]$/.test(value)) return "folder_like";
  return null;
}

/**
 * Same input file, as far as the client can tell: Windows-looking paths compare without case and
 * with either separator; others exactly. Symlinks, 8.3 names and `..` are not resolved.
 */
export function samePath(a: string, b: string): boolean {
  if (windowsLike(a) && windowsLike(b)) return a.replace(/\//g, "\\").toLowerCase() === b.replace(/\//g, "\\").toLowerCase();
  return a === b;
}
