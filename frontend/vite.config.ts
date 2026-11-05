import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import vuetify from "vite-plugin-vuetify";
import { defineConfig } from "vitest/config";

// The build is written into the Python package so wheels / PyInstaller can ship it.
// It is NOT committed (see the repository .gitignore); CI and developers run `npm run build`.
const outDir = fileURLToPath(new URL("../src/utteran_gui/web-next", import.meta.url));

// Dev server only: vuetify({ autoImport }) adds component imports while transforming, so the
// dependency scanner cannot see them and Vite re-optimizes and reloads on the first page load
// (in-flight requests then fail with "504 Outdated Optimize Dep"). Pre-bundle every component.
const vuetifyComponents = readdirSync(fileURLToPath(new URL("./node_modules/vuetify/lib/components", import.meta.url)), { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && /^V[A-Z]/.test(entry.name))
  .map((entry) => `vuetify/components/${entry.name}`);

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  optimizeDeps: {
    entries: ["index.html", "preview.html"],
    include: ["vuetify", "vuetify/directives", ...vuetifyComponents],
  },
  // Served under /next/; relative URLs keep the bundle independent of the mount path.
  base: "./",
  build: {
    outDir,
    emptyOutDir: true,
    assetsDir: "assets",
    // Every asset is a file: an inlined data: font or script would need a looser CSP.
    assetsInlineLimit: 0,
    sourcemap: false,
    target: "es2022",
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
