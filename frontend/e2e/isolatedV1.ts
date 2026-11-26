import { execFile, spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";
import { promisify } from "node:util";
import { expect, test as base } from "./fixtures";

const windows = process.platform === "win32";

export const test = base.extend<{
  holdSlowLaunch: boolean;
  setupE2e: boolean;
  backend: { root: string; url: string; releaseSlowLaunch: () => Promise<void> };
}>({
  holdSlowLaunch: [false, { option: true }],
  setupE2e: [false, { option: true }],
  backend: async ({ holdSlowLaunch, setupE2e }, use) => {
    const directory = await mkdtemp(join(tmpdir(), "utteran-s5b-e2e-"));
    const root = join(directory, "v1");
    const args = ["run", "--no-sync", "python", "../tools/ui2/serve_next_ui.py", "--port", "0", "--v1", "--data-root", root, "--exit-on-stdin-close"];
    if (holdSlowLaunch) args.push("--hold-slow-launch");
    if (setupE2e) args.push("--setup-e2e");
    const child = spawn("uv", args, {
      detached: !windows,
      env: { ...process.env, TMPDIR: directory, TEMP: directory, TMP: directory },
      stdio: ["pipe", "pipe", "inherit"],
    });
    const closed = new Promise<number | null>((resolve) => child.once("close", resolve));
    child.stdin.on("error", () => {
      // A failed startup may close the child's stdin before teardown.
    });
    const lines = createInterface({ input: child.stdout });
    try {
      const url = await new Promise<string>((resolve, reject) => {
        child.once("error", reject);
        child.once("exit", (code) => reject(new Error(`Synthetic server exited (${code})`)));
        lines.on("line", (line) => {
          const match = /^http:\/\/127\.0\.0\.1:\d+(?=\/launch\?)/.exec(line);
          if (match) resolve(match[0]);
        });
      });
      await expect.poll(async () => {
        const response = await fetch(`${url}/next/`, { signal: AbortSignal.timeout(1000) }).catch(() => null);
        if (!response) return null;
        await response.body?.cancel();
        return response.status;
      }, { timeout: 10_000 }).toBe(200);
      await use({
        root,
        url,
        releaseSlowLaunch: () => writeFile(join(root, "release-slow-launch"), ""),
      });
    } finally {
      // EOF stops the server and lets its real lifecycle join the synthetic dispatcher.
      child.stdin.end();
      const timer = new AbortController();
      const exit = await Promise.race([
        closed.finally(() => timer.abort()),
        delay(5_000, "timeout" as const, { signal: timer.signal }),
      ]);
      if (exit === "timeout" && child.pid) {
        // Bound a failed teardown to this fixture's own test process tree.
        if (windows) {
          await promisify(execFile)("taskkill", ["/pid", String(child.pid), "/t", "/f"]);
        } else {
          process.kill(-child.pid, "SIGKILL");
        }
        await closed;
      }
      lines.close();
      await rm(directory, { recursive: true, force: true });
      expect(exit, "isolated backend shutdown").toBe(0);
    }
  },
  baseURL: async ({ backend }, use) => {
    await use(backend.url);
  },
});
