import { createApp } from "vue";
import { createVuetify } from "vuetify";
import "vuetify/styles";
import "../../core/layout.css";
import App from "./App.vue";

const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
const vuetify = createVuetify({ theme: { cspNonce: nonce && !nonce.startsWith("__") ? nonce : undefined } });
createApp(App).use(vuetify).mount("#app");
