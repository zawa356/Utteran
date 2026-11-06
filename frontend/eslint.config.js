import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";

export default [
  { ignores: ["node_modules/**", "test-results/**", "playwright-report/**"] },
  ...tseslint.configs.recommended,
  ...pluginVue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: { parser: vueParser, parserOptions: { parser: tseslint.parser, sourceType: "module" } },
  },
  {
    rules: {
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // Views must go through host/ and api/; no direct bridge or network access.
      "no-restricted-globals": ["error", { name: "fetch", message: "Use src/api (injected http client)." }],
    },
  },
  {
    files: ["src/api/http.ts", "src/main.ts", "src/**/*.test.ts", "e2e/**/*.ts", "e2e-preview/**/*.ts"],
    rules: { "no-restricted-globals": "off" },
  },
  {
    files: ["src/features/**", "src/components/**", "src/App.vue"],
    rules: {
      "no-restricted-properties": [
        "error",
        { object: "window", property: "pywebview", message: "Use src/host." },
        { object: "window", property: "fetch", message: "Use src/api." },
      ],
    },
  },
  // UI-3-0 prototype isolation (docs/architecture/ui3/): the prototype never reaches api/, host/,
  // transport/ or the production AppCore, and production code never imports the prototype.
  {
    files: ["src/preview/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["**/api", "**/api/**", "**/host", "**/host/**", "**/transport/**"], message: "The UI-3-0 prototype must not reach the backend or the host bridge." },
            { group: ["**/domain/core", "**/composables/core", "**/domain/environment", "**/domain/settings"], message: "The prototype uses only synthetic state (src/preview/domain)." },
          ],
        },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "pywebview", message: "No host bridge in the prototype." },
        { object: "window", property: "fetch", message: "No network in the prototype." },
        { object: "navigator", property: "sendBeacon", message: "No network in the prototype." },
      ],
      "no-restricted-globals": [
        "error",
        { name: "fetch", message: "No network in the prototype." },
        { name: "XMLHttpRequest", message: "No network in the prototype." },
        { name: "EventSource", message: "No network in the prototype." },
        { name: "WebSocket", message: "No network in the prototype." },
        { name: "localStorage", message: "The prototype keeps state in memory only." },
        { name: "sessionStorage", message: "The prototype keeps state in memory only." },
      ],
    },
  },
  {
    files: ["src/**"],
    ignores: ["src/preview/**"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [{ group: ["**/preview", "**/preview/**"], message: "Production code must not import the UI-3-0 prototype." }] }],
    },
  },
];
