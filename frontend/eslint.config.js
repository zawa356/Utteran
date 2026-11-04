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
    files: ["src/api/http.ts", "src/main.ts", "src/**/*.test.ts", "e2e/**/*.ts"],
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
];
