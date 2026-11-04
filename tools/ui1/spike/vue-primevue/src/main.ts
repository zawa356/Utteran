import { createApp } from "vue";
import PrimeVue from "primevue/config";
import Aura from "@primeuix/themes/aura";
import "../../core/layout.css";
import App from "./App.vue";

const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
createApp(App)
  .use(PrimeVue, { theme: { preset: Aura }, csp: nonce && !nonce.startsWith("__") ? { nonce } : undefined })
  .mount("#app");
