import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.UI2_E2E_PORT ?? 8791);
export const BASE = `http://127.0.0.1:${PORT}`;

/**
 * E2E against the real FastAPI app (tools/ui2/serve_next_ui.py): real /launch cookie,
 * session middleware, per-response CSP nonce and built assets. Requires `npm run build`
 * first. Headless Chromium only — this is NOT a WebView2 acceptance.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-report.json" }]],
  use: {
    baseURL: BASE,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 820 },
  },
  webServer: {
    command: `uv run --no-sync python ../tools/ui2/serve_next_ui.py --port ${PORT}`,
    url: `${BASE}/next/`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "pipe",
  },
});
