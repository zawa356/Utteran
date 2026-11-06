import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.UI3_PREVIEW_PORT ?? 5180);
export const PREVIEW_BASE = `http://127.0.0.1:${PORT}`;

/**
 * UI-3-0 design prototype checks (frontend/preview.html on the Vite dev server). Synthetic data
 * only; no FastAPI, no /api. Separate from `npm run e2e` (UI-2, real app). Headless Chromium —
 * NOT a WebView2 / Windows DPI acceptance.
 */
export default defineConfig({
  testDir: "e2e-preview",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-preview-report.json" }]],
  outputDir: "test-results/preview-artifacts",
  use: {
    baseURL: PREVIEW_BASE,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 820 },
  },
  webServer: {
    command: `npx vite --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `${PREVIEW_BASE}/preview.html`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "pipe",
  },
});
