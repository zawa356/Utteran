# utteran

[日本語](README.md) | English

utteran is a local desktop app and CLI that creates speaker-aware transcripts from audio and video files.
It exports SRT, VTT, JSON, plain text, and Markdown without sending the recording to a cloud
transcription API.

> Development version: `0.1.0` (unreleased). The latest public snapshot is `v0.0.1`.
> APIs and configuration may change before 1.0.

## Supported environments

- Windows 10/11 (primary), Python 3.11 or 3.12
- Linux (secondary; model-free tests and imports run in CI)
- faster-whisper on CPU or NVIDIA CUDA
- whisper.cpp v1.9.1 on CPU, OpenVINO, Vulkan, or OpenVINO+Vulkan
- pyannote.audio 4.x diarization on CPU, NVIDIA CUDA, or Intel XPU

GPU, native-build, real-model, and long-audio behavior is verified by the acceptance harness on
matching hardware, not by CI.

## Windows quick start

```powershell
.\setup.ps1 -Profile cpu
.\run.ps1 models download faster-whisper:large-v3-turbo
.\run.ps1 transcribe .\input\meeting.mp4 --no-diarization
```

Run `.\start.ps1` for the numbered interactive menu. Available profiles are `cpu`, `cuda`,
`intel`, and `vulkan`; each uses an isolated virtual environment under `.venvs`.

For the desktop GUI, install its lightweight environment and at least one inference profile:

```powershell
.\setup.ps1 -Profile gui
.\setup.ps1 -Profile cuda
.\gui.ps1
```

`.venvs/win-gui` contains FastAPI, Uvicorn, and pywebview, but no PyTorch or faster-whisper.
The independent `utteran_gui` package never imports the inference core; it runs the selected
profile's `utteran` executable as a child process.
Phase 5a covers detection, run settings, progress, cancellation, and output-file listing. Transcript
view/search/history are planned for 5b, first-run setup for 5c, and installer packaging for 5d.

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
returned to the browser or API client. GUI settings do not retain input-file history.

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

## Development

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
