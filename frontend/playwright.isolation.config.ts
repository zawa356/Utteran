import { defineConfig } from "@playwright/test";

// This spec reads the production bundle from disk. It needs neither a browser
// nor the prototype dev server.
export default defineConfig({
  testDir: "e2e-preview",
  testMatch: "isolation.spec.ts",
  forbidOnly: true,
  workers: 1,
  retries: 0,
  reporter: "list",
});
