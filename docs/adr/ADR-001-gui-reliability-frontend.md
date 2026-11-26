# ADR-001: UI-1 GUI操作信頼性と次期フロントエンド構成

- Date: 2026-10-30
- Status: **Proposed — review required**（UI-1 #19、Epic #18）
- Examined main: `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`
- Scope: 静的コード監査、実関数抽出によるAPI競合モック。Windows配布版/E2E/実機推論は未検証。

## Decision (proposal)

1. **FastAPI + pywebview / Edge WebView2 を継続**。GUIをElectronへ全面移植する理由は現段階で確認できない。
2. **Vue 3 + TypeScript + Vite を第一候補**、**PrimeVue (MIT、ローカル同梱)** を第一候補のUIコンポーネントにする。UI-2着手前に、Windows VMの最小スパイクでWebView2/配布/CSP/i18n/仮想スクロールの可否を確認した後に採用確定する。React + TS + MUI、Svelte + TS、vanilla + Bootstrapも比較対象。
3. UI基盤は **操作状態モデル・API client・SSE/polling再同期** をコンポーネントより先に実装する。core/CLI/APIを契約の正本として残す。
4. **機能単位の段階移行とrollback**。移行済みビューを切り替え可能にし、既存のHTML/JSを直ちに除去しない。

## Observed architecture

`src/utteran_gui/web/app.js` (1,985 lines) holds mutable `state`, imperative DOM rendering, event listeners, `api()`, `EventSource` and polling. `index.html` has workspace, history/viewer, wizard, models, queue and settings. Python layers: `api.py` (FastAPI routes, session cookie, CSP, SSE), `app.py` (desktop bridge), `cli.py` (profile CLI boundary), `jobs.py` and `setup_wizard.py` (process/job lifecycle), `operation_queue.py` (queue), `history.py` / `settings.py`. `tests/test_gui.py` includes extensive Python/static asset assertions; no general browser click→request→render E2E was identified.

Event/API inventory:

| Feature | Frontend path | API / event path | Main concern |
| --- | --- | --- | --- |
| Environment + profile | `loadEnvironment` at 301 | GET /api/environment | response ordering |
| Start / finish | `startJob`, `handleEvent`, `finishJob` at 992-1176 | POST/GET /api/jobs, SSE events | duplicate finishes / stale job references |
| Wizard | `runWizardJob` at 670; `bindWizard` at 965 | POST/GET /api/wizard/jobs, SSE | polling rejects without local catch |
| Models | `runModelAction` at 1719 | POST /api/models/actions; GET wizard/jobs/{id} | poll failure / reconnect |
| Queue | `loadQueue` at 1784 | GET /api/queue; POST cancel | polling overlap and silent failure |
| History/viewer | `loadHistory`, `openViewer`, `regenerate` | history GET/POST/DELETE | response order / route navigation |
| Settings/token | `saveSettings`, `saveToken` | PATCH settings / token operations | inconsistent error feedback |
| Native file picker | `choosePath` at 1626 | pywebview bridge | separate native acceptance |

## Reproduced: stale environment response

Extracted the exact `async function loadEnvironment` implementation (lines 301-363) from main's `app.js`. Ran it with mocked DOM, `api`, select options and rendering dependencies in Playwright's browser JS context:

1. `loadEnvironment("cpu")` starts (response held).
2. `loadEnvironment("cuda")` starts, then resolves; `profile-summary` is CUDA.
3. Older CPU request resolves.
4. **Actual:** `profile-summary === "cpu"`, `state.environment.active_profile === "cpu"`. **Expected:** CUDA remains selected.

This proves a race in the isolated **production function** under intentionally reordered responses. It is **not** proof that the exact user-reported failure on a packaged Windows build has the same root cause. The call currently assigns `state.environment = await api(...)` with no request epoch/current-profile check.

## Static risks requiring tests

- `api()` lines 167-178 wraps `fetch` but no explicit timeout / AbortSignal / response correlation.
- `startJob` lines 1148-1169 enables start-button immediately after POST; SSE and a 1s status poll can both invoke `finishJob` (which performs a further GET without a terminal-update guard). Need deterministic overlap/re-entry tests rather than immediately declaring a user-visible bug.
- `runWizardJob` lines 696-716 uses `setInterval(async () => await api(...))`; a rejected polling request has no local catch, and on failure the timer/stream are not cleaned up in that callback.
- `loadQueue` runs every 1000ms at boot with `.catch(() => {})`; errors can be invisible and concurrent responses can reorder.
- `saveSettings` at 1817-1829, queue cancel at 1808-1812, and token clear at 1900-1905 lack local user-visible failure handling. Global `unhandledrejection` diagnostic exists, but that is not equivalent to feedback at the button.
- `runModelAction` at 1719 shows progress, but a transient polling exception moves to catch/finally without an established reconciliation of the accepted backend operation.
- `regenerate` at 1551 silently returns if no output formats selected; convey a validation message instead.

## Candidate comparison

| Candidate | State consistency / tests | Maintenance / WebView2 | Packaging / trade-off | Assessment |
| --- | --- | --- | --- | --- |
| Vanilla JS + Bootstrap | Manual state/event discipline remains | Small migration, familiar | Low build cost, but weak protection against current races | Not sufficient alone |
| Vue 3 + TS + PrimeVue | Reactive components, TS types, Vitest + Playwright | Incremental mount points, approachable templates; PrimeVue component coverage | Adds Node build/assets; confirm CSP, bundle size, accessibility | **Preferred for VM spike** |
| React + TS + MUI | Mature tooling and large ecosystem | Excellent tests but more glue for shared async state and component integration | Larger dependency surface and bundle risk; measure | Viable alternative |
| Svelte + TS + UI kit | Lean code/output, reactive state | Smaller component/test ecosystem than Vue/React | Smaller build but integration/tool maturity needs proof | Alternative |
| Electron host | Does not inherently solve state bugs | Replaces working native shell/bridge/distribution code | Chromium/runtime packaging, RAM, startup, signing/SAC/WDAC impact; measure against current host | Do not adopt without measured need |

**UI kit does not solve concurrency.** UI state and API/event transport remain separate decisions. All assets must be bundled locally; preserve no-CDN operation and session-cookie/CSP protections. Avoid duplicating core compatibility rules in TS.

## UI-2 detailed implementation scope (#20)

1. Introduce `frontend/` with Vue 3 + TypeScript + Vite proof of concept and local static build into a dedicated served asset directory. Keep `/api/*`, session cookie, WebView2 host, `/launch`, CLI and job storage compatible; add explicit build packaging test.
2. Define action state `idle | submitting | accepted | running | succeeded | failed | cancelling | cancelled | reconnecting`; show pending, disabled, success/failure, retry for every action.
3. Central `apiClient`: timeout, typed error mapping, cancellation, request epoch/latest-wins for **GET**; never auto-retry unsafe POST unless backend idempotency is established. Use request IDs/log correlation without exposing secrets.
4. Shared job controller keyed by job ID: SSE as progress hints, backend GET snapshot as authoritative for terminal state; single-flight polling, refresh on reconnect/visibility, cleanup at terminal/unmount, ignore stale job generation. Terminal transitions and cancel/complete races must be deterministic.
5. Preserve POST acceptance / operation ID and reconcile after lost connection rather than assuming a failed client response means backend operation failed. Review whether backend needs idempotency keys or event sequence/cursor; any contract change is a separately reviewed PR.
6. Add deterministic Vitest and Playwright tests: two GET responses reversed; POST pending/timeout; polling 401/500; SSE disconnected/duplicate/out of order; two simultaneous finish triggers; cancellation race; switching views; backend restart.
7. First migrate low-risk **settings/environment** while feature-flagging legacy view; then model management; job workspace/queue; wizard and history/viewer last, tracking full existing feature parity.

## Validation / rollback

Validation already done: read latest GitHub main, issues, plan, Python tests, GUI JS, and run isolated mocked race reproduction above. **Not done:** full Python tests, VM GUI boot, WebView2 native tests, bundled frontend build, GPU inference, installer/portable or SAC/WDAC checks; do not report these as pass.

Before merging UI-2 require: `uv lock --check`; `uv run ruff check src tests tools`; `uv run ruff format --check src tests tools`; `uv run mypy`; `uv run pytest -m "not requires_model"`; plus frontend lint/typecheck/unit/E2E and Windows VM WebView2 smoke. Test cookie/CSP + static asset routing with production build. Keep native desktop release metrics (start time, memory, size) for UI-4 #22.

Rollback by per-view feature switch and PR revert; don't change job/resume/state formats or overwrite user data. Real Windows native dialogs, packaged variants and GPU behavior remain #22/#15/#7.

## Open questions / exit criteria before accepting ADR

- Measure Vue/PrimeVue vs React/MUI minimal spike on WebView2 in Windows VM: production asset size, CSP compliance, accessibility, startup and runtime memory.
- Prove in browser E2E the stale profile race and fix. User-reported clicks/refresh symptoms remain unconfirmed until reproduced on current package or captured with sanitized diagnostics.
- Verify `EventSource` reconnect behavior and backend stream cursor/dedup contract before selecting any client event sequence policy.
- Confirm embedded static asset paths and packaging in installer/portable/Python modes.

References: #18 #19 #20 #21 #22 #6 #12 #15.