import { createApp } from "vue";
import { createVuetify, type ThemeDefinition } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";
import "vuetify/styles";
import "./styles/app.css";
import "./styles/v1.css";
import App from "./App.vue";
import { CORE } from "./composables/core";
import { createCore } from "./domain/core";
import { browserEventSource } from "./domain/job";
import { readApiMode } from "./domain/v1/apiMode";
import { createPywebviewHost } from "./host/pywebview";
import { componentDefaults, darkTheme, lightTheme, v1Defaults } from "./styles/theme";
import { blueDark, blueLight, greenDark, greenLight } from "./styles/palette";

// The server encodes 16 random bytes as 22 unpadded base64url characters. The
// 21-character template placeholder does not match the server format.
const nonceMeta = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content ?? "";
const cspNonce = /^[A-Za-z0-9_-]{22}$/.test(nonceMeta) ? nonceMeta : undefined;

const host = createPywebviewHost(window);
// The API mode is whatever the server wrote into this page (legacy unless it is exactly "v1-dev").
const core = createCore({ fetch: window.fetch.bind(window), host, eventSource: browserEventSource(), apiMode: readApiMode(document) });
// Release the job poller/SSE when the page goes away (the server job itself keeps running).
window.addEventListener("pagehide", () => core.dispose());

window.addEventListener("error", (event) => {
  host.reportError({ kind: "error", message: String(event.message), source: event.filename, line: event.lineno, column: event.colno });
});
window.addEventListener("unhandledrejection", (event) => {
  host.reportError({ kind: "unhandledrejection", message: String(event.reason) });
});

const v1 = core.apiMode === "v1-dev";
const themes: Record<string, ThemeDefinition> = v1 ? { light: blueLight, dark: blueDark, "green-light": greenLight, "green-dark": greenDark } : { light: lightTheme, dark: darkTheme };
const vuetify = createVuetify({
  theme: { defaultTheme: "system", themes, cspNonce },
  icons: { defaultSet: "mdi", aliases, sets: { mdi } },
  defaults: v1 ? v1Defaults : componentDefaults,
});

const app = createApp(App);
app.config.errorHandler = (error) => host.reportError({ kind: "vue", message: String(error) });
app.provide(CORE, core);
app.use(vuetify).mount("#app");
