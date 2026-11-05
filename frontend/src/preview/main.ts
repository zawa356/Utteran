import { createApp } from "vue";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";
import "vuetify/styles";
import "../styles/app.css";
import "./preview.css";
import { PREVIEW } from "./composables";
import { DemoController } from "./domain/demo";
import { LayoutController } from "./domain/layout";
import PreviewApp from "./PreviewApp.vue";
import { SetupController } from "./domain/setup";
import { blueDark, blueLight, greenDark, greenLight, previewDefaults } from "./theme";

/**
 * UI-3-0 design prototype entry (frontend/preview.html). Reachable only from the Vite dev server:
 * `vite build` emits index.html alone, and this guard refuses to start in any production bundle.
 * No api/, host/ or AppCore is created here — the prototype cannot reach the backend.
 */
if (!import.meta.env.DEV) throw new Error("The UI-3-0 prototype runs only on the Vite dev server.");

const vuetify = createVuetify({
  // Prototype-only accent themes (green UI-3 / blue UI-2 hue); the production theme is not used here.
  theme: { defaultTheme: "greenLight", themes: { greenLight, greenDark, blueLight, blueDark } },
  icons: { defaultSet: "mdi", aliases, sets: { mdi } },
  defaults: previewDefaults,
});

const app = createApp(PreviewApp);
const demo = new DemoController();
const setup = new SetupController({
  applyEnvironment: (env, diarizationReady) => demo.setEnvironment(env, diarizationReady),
  applyDefaults: (patch) => demo.setCommonPatch(patch),
  notify: (key, params) => demo.say(key, params),
});
app.provide(PREVIEW, { demo, layout: new LayoutController(), setup });
app.use(vuetify).mount("#preview");
