import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import vuetify from "vite-plugin-vuetify";
import { defineConfig } from "vitest/config";

// The build is written into the Python package so wheels / PyInstaller can ship it.
// It is NOT committed (see the repository .gitignore); CI and developers run `npm run build`.
const outDir = fileURLToPath(new URL("../src/utteran_gui/web-next", import.meta.url));

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
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
