import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.UI2_E2E_PORT ?? 8791);
export const BASE = `http://127.0.0.1:${PORT}`;
const V1_PORT = Number(process.env.UI2_E2E_V1_PORT ?? 8792);
export const V1_BASE = `http://127.0.0.1:${V1_PORT}`;
// S5-A retains its history across its own tests, including the paused paging/unknown sequence.
const S5A_PORT = Number(process.env.UI2_E2E_S5A_PORT ?? 8793);
const S5A_BASE = process.env.UI2_S5A_BASE = `http://127.0.0.1:${S5A_PORT}`;
process.env.UI2_S5A_ROOT ??= mkdtempSync(join(tmpdir(), "utteran-s5a-e2e-"));
// The remaining v1 specs use a separate synthetic store.
process.env.UI2_V1_ROOT ??= mkdtempSync(join(tmpdir(), "utteran-v1-e2e-"));
export const V1_ROOT = process.env.UI2_V1_ROOT;

/**
 * E2E against the real FastAPI app (tools/ui2/serve_next_ui.py): real /launch cookie,
 * session middleware, per-response CSP nonce and built assets. Requires `npm run build`
 * first. Headless Chromium only — this is NOT a WebView2 acceptance.
 *
 * The legacy project uses the Vue + legacy API server. The v1 servers use real B5/B7
 * on the loopback authority with synthetic B4 evidence and runner. S5-B starts a separate
 * backend per test (e2e/isolatedV1.ts).
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-report.json" }]],
  use: {
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 820 },
  },
  projects: [
    { name: "legacy", testIgnore: /\.v1\.spec\.ts$/, use: { baseURL: BASE } },
    { name: "v1", testMatch: /\.v1\.spec\.ts$/, use: { baseURL: V1_BASE } },
  ],
  webServer: [
    {
      command: `uv run --no-sync python ../tools/ui2/serve_next_ui.py --port ${PORT}`,
      url: `${BASE}/next/`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
    },
    {
      command: `uv run --no-sync python ../tools/ui2/serve_next_ui.py --port ${S5A_PORT} --v1 --data-root "${process.env.UI2_S5A_ROOT}"`,
      url: `${S5A_BASE}/next/`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
    },
    {
      command: `uv run --no-sync python ../tools/ui2/serve_next_ui.py --port ${V1_PORT} --v1 --data-root "${V1_ROOT}"`,
      url: `${V1_BASE}/next/`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
    },
  ],
});
