# frontend — Utteran next GUI (UI-2)

Vue 3 + TypeScript + Vite + Vuetify 4. Design and status: [docs/architecture/ui2/](../docs/architecture/ui2/README.md).
The build is written to `src/utteran_gui/web-next/` (git-ignored) and is served **only** when the GUI is started
with `--next-ui`. Without that flag nothing here is used.

## Requirements

- Node 22 (`.nvmrc`; `engines` `>=22.12 <23`)
- npm 11.21.1 (`packageManager`). npm 10.9.9 crashes while *regenerating* this lockfile
  (`Cannot read properties of null (reading 'edgesOut')`); `npm ci` from the committed lockfile also works with npm 10.
- `uv` (the E2E server runs the real FastAPI app from this checkout)

## Commands

```bash
npm ci
```

```bash
npm run lint && npm run typecheck && npm test && npm run build
```

```bash
npx playwright install chromium && npm run e2e
```

```bash
npm run audit:prod
```

Open the next UI in the native window (after `npm run build`):

```bash
uv run utteran-gui --next-ui
```

Or in a browser against the real app with synthetic services (prints the launch URL):

```bash
uv run python ../tools/ui2/serve_next_ui.py --port 8791
```

`UI2_SCREENSHOTS=1 npm run e2e` additionally writes screenshots (synthetic data) to `test-results/screenshots/`.

## Rules

- Views (`features/`, `components/`) use only `domain/` actions/stores and `host/`; ESLint rejects `window.pywebview`
  and direct `fetch` there.
- No CDN, remote fonts or license-key packages. Production dependencies must pass `npm run audit:prod`.
- Never commit `node_modules/`, the build output, screenshots, real paths, tokens, recordings or transcripts.
