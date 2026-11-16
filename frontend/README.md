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

The `--next-ui-v1` shape (UI-3 S5-A: real B5/B7 `/api/v1`, synthetic B4 evidence and worker runner; the folder must be empty or absent):

```bash
uv run python ../tools/ui2/serve_next_ui.py --port 8792 --v1 --data-root <empty folder>
```

`npm run e2e` runs two Playwright projects: `legacy` (`--next-ui`, every existing spec) and `v1` (`e2e/*.v1.spec.ts`,
a fresh temporary data root per run). `UI2_SCREENSHOTS=1 npm run e2e` additionally writes screenshots (synthetic data) to `test-results/screenshots/`.

### UI-3-0 design prototype (dev server only)

A clickable mock-up of the UI-3 screens with synthetic data only ([docs/architecture/ui3/](../docs/architecture/ui3/README.md)).
It lives in `src/preview/` + `preview.html`, is **not** part of `npm run build` (only `index.html` is built) and never
calls the backend, the host bridge or storage (enforced by ESLint and by the E2E). No `uv` needed.
Iteration 2 ([docs/architecture/ui3/iteration-2.md](../docs/architecture/ui3/iteration-2.md)) adds the Normal top bar with one
file, the resizable Expert sidebar and panes, a prototype-only green palette (`src/preview/theme.ts`; the production
`src/styles/theme.ts` is unchanged) and fictional environment examples. Iteration 3
([docs/architecture/ui3/iteration-3.md](../docs/architecture/ui3/iteration-3.md)) raises the readability floor (prototype-only
Vuetify defaults `previewDefaults`), adds the shared app menu with mock log/diagnostics/about dialogs, the Expert
files → settings → submit flow with a short queue, Normal/Expert settings, and a fictional stage progress/ETA mock.
Iteration 4 ([docs/architecture/ui3/iteration-4.md](../docs/architecture/ui3/iteration-4.md)) adds the categorised Expert
processing settings (prompt, glossary, engine-specific fields), a mock setup wizard (first run / add / repair, switchable
in the review strip), log and preset import/export confirmation screens (nothing is written), the green/blue × appearance
theme setting and the existing app icon (`src/preview/assets/utteran.svg`, a byte-identical copy of `icon/utteran.svg`).

```bash
npm run prototype
```

Then open `http://127.0.0.1:5180/preview.html`. Checks (run `npm run build` first; the isolation spec inspects the build):

```bash
npm run e2e:prototype
```

`UI3_SCREENSHOTS=1 npm run e2e:prototype` writes the review set to `test-results/ui3-screenshots/` (run it after
`npm run e2e`, which recreates `test-results/`).

## Rules

- Views (`features/`, `components/`) use only `domain/` actions/stores and `host/`; ESLint rejects `window.pywebview`
  and direct `fetch` there.
- No CDN, remote fonts or license-key packages. Production dependencies must pass `npm run audit:prod`.
- Never commit `node_modules/`, the build output, screenshots, real paths, tokens, recordings or transcripts.
