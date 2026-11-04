import { createApp } from "vue";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";
import "vuetify/styles";
import "./styles/app.css";
import App from "./App.vue";
import { CORE } from "./composables/core";
import { createCore } from "./domain/core";
import { createPywebviewHost } from "./host/pywebview";
import { componentDefaults, darkTheme, lightTheme } from "./styles/theme";

// The server writes a fresh nonce per response (utteran_gui.next_ui). A leftover placeholder
// means the page was not served by Utteran: then no nonce is used and CSP blocks inline styles.
const nonceMeta = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content ?? "";
const cspNonce = /^[A-Za-z0-9_-]{16,}$/.test(nonceMeta) && !nonceMeta.startsWith("__") ? nonceMeta : undefined;

const host = createPywebviewHost(window);
const core = createCore({ fetch: window.fetch.bind(window), host });

window.addEventListener("error", (event) => {
  host.reportError({ kind: "error", message: String(event.message), source: event.filename, line: event.lineno, column: event.colno });
});
window.addEventListener("unhandledrejection", (event) => {
  host.reportError({ kind: "unhandledrejection", message: String(event.reason) });
});

const vuetify = createVuetify({
  theme: { defaultTheme: "system", themes: { light: lightTheme, dark: darkTheme }, cspNonce },
  icons: { defaultSet: "mdi", aliases, sets: { mdi } },
  defaults: componentDefaults,
});

const app = createApp(App);
app.config.errorHandler = (error) => host.reportError({ kind: "vue", message: String(error) });
app.provide(CORE, core);
app.use(vuetify).mount("#app");
