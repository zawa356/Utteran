import { createApp } from "vue";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";
import "vuetify/styles";
import "../styles/app.css";
import "./preview.css";
import { componentDefaults, darkTheme, lightTheme } from "../styles/theme";
import { PREVIEW } from "./composables";
import { DemoController } from "./domain/demo";
import { LayoutController } from "./domain/layout";
import PreviewApp from "./PreviewApp.vue";
import { greenDark, greenLight } from "./theme";

/**
 * UI-3-0 design prototype entry (frontend/preview.html). Reachable only from the Vite dev server:
 * `vite build` emits index.html alone, and this guard refuses to start in any production bundle.
 * No api/, host/ or AppCore is created here — the prototype cannot reach the backend.
 */
if (!import.meta.env.DEV) throw new Error("The UI-3-0 prototype runs only on the Vite dev server.");

const vuetify = createVuetify({
  // Prototype-only green proposal next to the unchanged production (UI-2) themes for comparison.
  theme: { defaultTheme: "greenLight", themes: { light: lightTheme, dark: darkTheme, greenLight, greenDark } },
  icons: { defaultSet: "mdi", aliases, sets: { mdi } },
  defaults: componentDefaults,
});

const app = createApp(PreviewApp);
app.provide(PREVIEW, { demo: new DemoController(), layout: new LayoutController() });
app.use(vuetify).mount("#preview");
