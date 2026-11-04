import { createRoot } from "react-dom/client";
import createCache from "@emotion/cache";
import { CacheProvider } from "@emotion/react";
import { CssBaseline } from "@mui/material";
import "../../core/layout.css";
import { App } from "./App";

const raw = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
const nonce = raw && !raw.startsWith("__") ? raw : undefined;
const cache = createCache({ key: "mui", nonce });
createRoot(document.getElementById("app")!).render(
  <CacheProvider value={cache}>
    <CssBaseline />
    <App />
  </CacheProvider>,
);
