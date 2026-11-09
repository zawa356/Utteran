# utteran

[日本語](README.md) | English

utteran is a local desktop app and CLI that creates speaker-aware transcripts from audio and video files.
It exports SRT, VTT, JSON, plain text, and Markdown without sending the recording to a cloud
transcription API.

> Current development version: `0.1.44` (unreleased).
> APIs and configuration may change before 1.0.

> The current backend (Phase A-B9) structure, feature status, limits and test coverage are maintained in
> [docs/architecture/README.md](docs/architecture/README.md). The B5-B9 v1 APIs are all **merged into main,
> unreleased and explicit opt-in**; normal GUI/CLI startup does not enable them.

> Next GUI (UI-2, preview): a Vue 3 + Vuetify 4 frontend foundation is opened only with the developer flag
> `utteran-gui --next-ui` after building `frontend/`. The default GUI is unchanged (legacy UI). Migrated so far: the runtime
> environment view and the settings page (UI-3 S1: theme, language, default profile, default input/output folders with the
> native folder dialog, Hugging Face token register/delete, log location). Setup is handed over to the legacy screen; see
> [docs/architecture/ui3/s1-settings-and-host.md](docs/architecture/ui3/s1-settings-and-host.md) (Japanese; 0.1.44, not yet accepted on Windows WebView2).

> **UI-3-0 design freeze (2026-11-06):** The fifth iteration is the accepted UI/UX design baseline; [PR #38](https://github.com/zawa356/Utteran/pull/38) is merged. See the [design and S1–S5 handoff](docs/architecture/ui3/design-freeze.md) (Japanese). The prototype uses synthetic data and runs **only on the Vite development server**; neither the legacy GUI nor the production `--next-ui` UI was changed. Production migration and Windows/WebView2 acceptance remain open.

### Phase B9 events, sync and diagnostics (merged into main, unreleased)

0.1.38 adds SSE `GET /api/v1/events` (committed events only, `epoch:seq` IDs, `Last-Event-ID`
reconnect, 410 or `resync_required` when a cursor expires), `GET /api/v1/sync` (resources, queue and
cursor from one read transaction; pages never mix revisions) and sanitized diagnostics (fixed codes,
timestamps, stage and attempt only). The outbox is compacted only for events older than 7 days and
beyond the newest 100,000, at explicit activation. **The default GUI/CLI and the legacy SSE are
unchanged** (opt-in `enable_v1_events=True`); no new UI is wired yet. See
[Phase B9](docs/architecture/b9/README.md).


### Phase B8 setup plan, approval and durable setup execution (merged into main, unreleased)

0.1.37 adds an experimental v1 Setup API: a read-only plan, an explicit user approval, then execution
of exactly what was approved. A plan lists the target profile, models, preserved existing
environments, the step DAG, size estimates (null when unknown) and required agreements/tokens; an
approval is bound to the plan hash, the environment observation fingerprint, an expiry and the
agreement IDs the user actually selected. Execution shares the single B7 heavy slot and journals
intent -> child -> PID -> exit -> verification per step; nothing is recorded as succeeded without
verification. **The default GUI/CLI and the legacy setup wizard are unchanged**; the API exists only
with `create_app(..., enable_v1_jobs=True, enable_v1_setup=True, ...)`. Only faster-whisper/pyannote
models and creating a new profile (Windows) can run; whisper.cpp, OpenVINO and rebuilding an
existing venv are refused. A setup success is not B4 ready evidence. No real-hardware, GPU or real
download acceptance has been performed. See [Phase B8](docs/architecture/b8/README.md).


### Phase B7 immutable job submission, durable queue controls and safe dispatch (merged into main, unreleased)

0.1.36 adds an experimental v1 Job/Queue API. Validated settings, the input identity (including a
content SHA-256) and the B4 resolution are frozen per job as an immutable private snapshot, and a
multi-file submission is accepted all-or-nothing in one transaction. Queue-revision CAS reorder,
hold and dispatch pause, cancel/retry/rerun/revalidate, single-slot dispatch to the B3 worker and
conservative recovery based on OS process identity are included. **The default GUI/CLI is
unchanged**: the API exists only with `create_app(..., enable_v1_settings=True,
enable_v1_jobs=True, v1_authority=...)`, which also closes the legacy queue for new work. Only the
limited B3 worker configuration (faster-whisper, explicit local model, no diarization, single file)
with B4 ready evidence can run; standard environments have no smoke evidence, so submissions are
normally refused (fail closed). whisper.cpp, OpenVINO GenAI, pyannote and glossary are refused and
never redirected to the legacy CLI. The private DB is unencrypted with no retention limit. See
[Phase B7](docs/architecture/b7/README.md).


### Phase B6 durable control store (merged into main, unreleased)

0.1.35 adds an independent SQLite control store (`utteran_control`) for future Job/Setup/Refresh
operations. State, revisions, queue metadata, idempotency records, and a public outbox commit in one
transaction, and an explicitly invoked startup recovery is conservative (queue paused, waiting items
held, unverifiable owners flagged `recovery_required`). The default GUI/CLI neither creates nor uses
it; the existing queue and job execution are unchanged. B6 alone provides no setup execution and no HTTP
exposure such as `/api/v1/events` or `/sync` (added later by B8-B9, both opt-in); job submission/dispatch is the opt-in B7 above. Exactly-once
execution of external processes is not guaranteed. Private payloads are stored unencrypted. See
[Phase B6](docs/architecture/b6/README.md).

### Phase B5 development API (merged into main, unreleased)

0.1.34 adds independent Preset/Policy revision storage and a private settings inheritance preview.
Developers must opt in with `create_app(..., enable_v1_settings=True,
v1_authority="127.0.0.1:<bound port>")`. The usual GUI keeps its existing behavior.
Automatic import, job submission, model downloads, and new screens are not provided.
Saving settings does not establish worker support or execution readiness. The database and preview
history can contain private prompts and are not encrypted. See [Phase B5](docs/architecture/b5/README.md)
for storage, retention, recovery, and unverified areas.


## Supported environments

- Windows 10/11 (primary), Python 3.11 or 3.12
- Linux (secondary; model-free tests and imports run in CI)
- faster-whisper on CPU or NVIDIA CUDA
- whisper.cpp v1.9.2 on CPU, OpenVINO, Vulkan, or OpenVINO+Vulkan
- pyannote.audio 4.x diarization on CPU, NVIDIA CUDA, or Intel XPU

GPU, native-build, real-model, and long-audio behavior is verified by the acceptance harness on
matching hardware, not by CI.

## Choose a Windows launch path

Use `utteran-setup-<version>.exe` for regular, ongoing use. It keeps profiles, models, settings,
jobs, and logs in the existing per-user Windows locations. Use
`utteran-portable-<version>.zip` for temporary use: extract it and run `utteran-gui.exe`; all
utteran-managed data is created under the extracted `data` folder, so removing the extracted
folder removes that data.

There is a third path for Smart App Control environments, policies that prohibit unsigned app
executables, and users who already maintain Python environments. From a source checkout, install
Python 3.11/3.12, uv, and ffmpeg yourself, then run `launch-python.bat`. It checks prerequisites
without installing them, uses `ExecutionPolicy Bypass` only for that invocation, records a
diagnostic log, and starts both the GUI and profile commands through the venv `python.exe -m`
entry points. Its venv stays under the repository; other managed data uses the normal per-user
locations.

This path has not been verified to bypass Smart App Control. Python may start while native DLLs
from torch, OpenVINO, OpenVINO GenAI, or a locally built whisper.cpp are still blocked, so only
some profiles may work. This statement will be updated after testing on an enabled machine.

`build.ps1` places only the installer, portable ZIP, and their `.sha256` sidecars in
`dist/release`. `dist/staging` contains intermediate files and is not distributed.

The portable build does not save a Hugging Face token; enter it again on every launch. Profiles
and models consume several GB after setup, moving the extracted folder can require rebuilding its
venv, and USB storage can be slow. It uses the same executable as the installer build and does not
bypass Smart App Control.

## Windows quick start

```powershell
.\setup.ps1 -Profile cpu
.\run.ps1 models download faster-whisper:large-v3-turbo
.\run.ps1 transcribe .\input\meeting.mp4 --no-diarization
```

Run `.\start.ps1` for the numbered interactive menu. Available profiles are `cpu`, `cuda`,
`intel`, and `vulkan`; each uses an isolated virtual environment under `.venvs`.

For the desktop GUI, install its lightweight environment and launch it; a first-run setup wizard
detects your hardware and walks through building the recommended inference profile, installing uv,
fetching a model, configuring a token, and verifying everything actually works (Phase 5c):

```powershell
.\setup.ps1 -Profile gui
.\gui.ps1
```

You can still build an inference profile manually first with `.\setup.ps1 -Profile cuda` (or
another profile) - the wizard just calls `setup.ps1` itself, so either order produces the same
result. `.venvs/win-gui` contains FastAPI, Uvicorn, and pywebview, but no PyTorch or faster-whisper.
The independent `utteran_gui` package never imports the inference core; it runs the selected
profile's `utteran` executable as a child process. The GUI includes a virtualized transcript viewer,
full-text search, speaker/time filters, prominent model and device metadata, job history, deletion,
and export-only regeneration with new formats, speaker display names, or an output directory.
Regeneration reads the job's `merged.json`; it does not rerun ASR or diarization.

## Linux installation

Install [uv](https://docs.astral.sh/uv/), Python 3.11/3.12, and ffmpeg, then run:

```console
uv sync --extra cpu
uv run utteran devices
uv run utteran transcribe audio.wav --no-diarization
```

The `cpu`, `cuda`, and `xpu` extras are mutually exclusive.

## Models and diarization

Models are never downloaded silently. Use:

```console
uv run utteran models list --available
uv run utteran models download
uv run utteran models verify
```

`pyannote/speaker-diarization-community-1` requires accepting its terms on Hugging Face and
providing a read token through `HF_TOKEN`, a Git-ignored `.env`, or the OS keyring. Never put a
token in `config.toml`, a command line, an issue, or a log. Revoke a leaked token before attempting
history cleanup. A token saved through the GUI is stored only in the OS keyring and is never
returned to the browser or API client. GUI settings do not retain input-file history, transcript
text, or search terms. Speaker display names are stored only beside the associated job and are
removed with that job. Transcript API responses use `Cache-Control: no-store`.

Machine-readable history and export-only regeneration are also available from the CLI:

```console
uv run utteran jobs list --json
uv run utteran jobs show <job_id> --json
uv run utteran jobs export <job_id> --format txt,json --output-dir transcripts \
  --speaker-label SPEAKER_00=Alice
uv run utteran jobs clean --job-id <job_id>
```

For machine-readable progress, add `--progress-json --quiet`. The CLI writes one UTF-8 JSON object
per stderr line, including stages and output paths but never transcript segments, words, or text.

## Benchmarking

Backend rankings can change with audio length. On one Intel Arc 140T system, Vulkan was faster on
180 seconds, while OpenVINO+Vulkan was substantially faster on a 24m46s recording. This is one
observation, not a universal hardware claim.

The default is to measure the full input WAV. Use at least 15 minutes for long-form decisions, or
compare several lengths in one command:

```console
uv run utteran benchmark --audio long-sample.wav \
  --durations 180,900,full --variants vulkan,openvino_vulkan \
  --json benchmark.json
```

Short measurements include a warning in both console and JSON output. Benchmarking does not retain
recognized text or pipeline jobs.

## GUI/UX modernization roadmap

The next development focus is GUI reliability and a GUI-first workflow, while keeping the
existing core and CLI as the execution foundation. The current desktop shell is
**FastAPI + pywebview / Edge WebView2, not Electron**. Bootstrap and other UI kits,
frontend frameworks, and host alternatives are candidates to evaluate, not decisions.

This is **planned work, not implemented yet**. See the [UI/UX modernization plan and
handoff (Japanese)](docs/UI_UX_刷新計画.md), [Epic #18](https://github.com/zawa356/Utteran/issues/18),
and the first phase [#19 (audit and technology selection)](https://github.com/zawa356/Utteran/issues/19).

2026-11-04: the UI-1 audit, reproductions and a **proposed** technology choice are in
[docs/architecture/ui1/](docs/architecture/ui1/README.md) (Japanese). The new GUI is **not implemented**, and
[ADR-003](docs/adr/ADR-003-ui-frontend-stack-and-host-boundary.md) awaits the owner's decision.


```console
uv sync --extra dev --extra gui
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run mypy
uv run pytest -m "not requires_model"
uv lock --check
```

See the [Japanese README](README.md) for the Japanese quick-start and command overview, the
[requirements](要件定義.md) for the normative specification, and the
[release procedure](docs/リリース手順.md) for versioning and release gates.

## License and model terms

utteran source code is licensed under the [MIT License](LICENSE). Dependencies, tools, drivers,
and models have separate licenses and terms. Review each current model card and accept any required
terms before use or redistribution. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
