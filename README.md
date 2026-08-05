# utteran

[English](README.en.md) | 日本語

utteranは、音声・動画から話者別の文字起こしをローカル生成するCLIです。
会議・インタビュー・講演を、SRT / VTT / JSON / TXT / Markdownへ出力します。
入力音声をクラウド文字起こしAPIへ送信しません。

> 開発版: `0.1.0`（未release）。直近の公開snapshotは`v0.0.1`です。API・設定は1.0まで変更されます。

## 主な機能

- faster-whisper: CPU / NVIDIA CUDA
- whisper.cpp v1.9.1: CPU / OpenVINO / Vulkan / OpenVINO+Vulkan
- pyannote.audio 4.x話者分離: CPU / NVIDIA CUDA / Intel XPU
- 単一ファイル／folder batch、段階別resume、5形式出力
- profile別venv、model／job／native build管理、device診断
- Windows番号menu (`start.ps1`) と自動化向けCLI

主対象はWindows 10/11、Python 3.11/3.12です。Linuxは副対象で、CIがモデル不要testとimportを
確認します。GPU、native build、実model、長時間処理は対象hardware上の受入試験で保証します。

## 5分で始める（Windows / CPU）

PowerShellでrepository直下から実行します。管理者権限は不要です。

```powershell
.\setup.ps1 -Profile cpu
.\run.ps1 models download faster-whisper:large-v3-turbo
.\run.ps1 transcribe .\input\meeting.mp4 --no-diarization
```

番号menuを使う場合:

```powershell
.\start.ps1
```

話者分離にはpyannote modelの利用条件への同意とHugging Face tokenが必要です。取得前に必ず
[ライセンスとモデル利用条件](#ライセンスとモデル利用条件)を確認してください。

## Install profile

```powershell
.\setup.ps1 -Profile cpu
.\setup.ps1 -Profile cuda
.\setup.ps1 -Profile intel
.\setup.ps1 -Profile vulkan
.\setup.ps1 -List
.\setup.ps1 -SetDefault intel
```

| Profile | 用途 | 主な依存 |
|---|---|---|
| `cpu` | GPUなし | CPU PyTorch、faster-whisper、pyannote |
| `cuda` | NVIDIA | CUDA 12.6 PyTorch、faster-whisper、pyannote |
| `intel` | Intel Arc/NPU | XPU PyTorch、OpenVINO、whisper.cpp |
| `vulkan` | AMD等 | CPU PyTorch、Vulkan whisper.cpp |

各venvは`.venvs/<os>-<profile>`へ分離されます。model、job、native buildはprofile間で共有します。
`setup.ps1`は依存、ffmpeg、device診断だけを担当し、modelを暗黙downloadしません。

PowerShell 5.1では全`.ps1`のUTF-8 BOMが必須です。BOMを除去するformatterをかけないでください。

### Linux

```console
uv sync --extra cpu
sudo apt install ffmpeg                # Debian / Ubuntu
uv run utteran devices
uv run utteran transcribe audio.wav --no-diarization
```

Intel profile相当は`--extra xpu --extra whisper-cpp --extra openvino`、Vulkan profile相当は
`--extra cpu --extra whisper-cpp`です。`cpu`/`cuda`/`xpu` extrasは同時指定できません。

## Modelとtoken

```console
uv run utteran models list --available
uv run utteran models download
uv run utteran models verify
uv run utteran models path
```

pyannote community-1を使う場合:

1. [model page](https://huggingface.co/pyannote/speaker-diarization-community-1)で条件に同意する。
2. read tokenを発行する。
3. `HF_TOKEN`環境変数、repository直下の`.env`、OS keyringのいずれかへ保存する。

```powershell
$env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxxxxx"
.\run.ps1 models download pyannote:pyannote/speaker-diarization-community-1
```

tokenを`config.toml`、command line、issue、logへ書かないでください。`.env`はGit除外され、runtimeの
log／例外はtoken形式をmaskしますが、漏えい時は履歴修正より先にtokenを失効してください。

## 文字起こし

```console
uv run utteran transcribe meeting.mp4
uv run utteran transcribe interview.wav --num-speakers 2 --format srt,vtt,json,txt,md
uv run utteran transcribe lecture.m4a --no-diarization
uv run utteran transcribe recordings/ --recursive --include "**/*.wav"
```

既定でresumeは有効です。設定変更時は影響stage以降だけを再実行し、出力形式だけの変更はexportだけを
やり直します。`--force`は全stage再実行、`--no-resume`はcache不使用です。batchは個別失敗後も
継続し、一部失敗はexit 5、全件失敗は1、Ctrl+Cは130を返します。

ASRと話者分離のdeviceは別に指定できます。

```console
uv run utteran transcribe meeting.wav --asr-backend whisper-cpp \
  --asr-device openvino_vulkan --diarization-device xpu:0
```

## whisper.cppとbenchmark

```console
uv run utteran native build
uv run utteran models download whisper-cpp:large-v3-turbo-q5_0
uv run utteran models prepare-openvino whisper-cpp:large-v3-turbo-q5_0 --device GPU
uv run utteran transcribe meeting.wav --asr-backend whisper-cpp
```

VulkanとOpenVINO+Vulkanの順位は音声長で変わります。Intel Arc 140Tで、180秒ではVulkan
15.363秒／OpenVINO+Vulkan 19.638秒だった一方、24分46秒では110.730秒／65.141秒と逆転しました。
単一環境・素材の観測であり、他環境へ一般化はできません。

既定benchmark長は指定WAV全体です。15分以上を推奨し、短い測定には上記の逆転を示す警告が出ます。

```console
uv run utteran benchmark --audio long-sample.wav \
  --durations 180,900,full --variants vulkan,openvino_vulkan \
  --json benchmark.json
uv run utteran benchmark --audio long-sample.wav --apply
```

`benchmark`は認識本文やjobを保存しません。複数長の`--apply`は最長結果を採用し、測定秒数も
configへ記録します。OpenVINO構成は事前にencoder IR生成が必要です。`auto`はIR未生成でも動く
Vulkanを現在優先します。

## 設定と管理

```console
uv run utteran devices --json
uv run utteran profiles list
uv run utteran jobs list
uv run utteran config init
uv run utteran config show
```

一般設定の優先順位は`CLI 引数 > 環境変数 > .env > config.toml > 既定値`です。
トークンの参照元は環境変数、`.env`、OS キーリングの3段階で、この順に優先します。全設定、終了code、JSON schema、resume hashは
[要件定義](要件定義.md)を参照してください。

## 開発と品質保証

```console
uv sync --extra dev
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run mypy
uv run pytest -m "not requires_model"
uv lock --check
```

CIはLinux/Windowsのモデル不要testと静的検査を行います。native build、実model、GPU、長時間、性能は
CI対象外です。release品質は[統合受入試験ハーネス](tools/acceptance/README.md)で確認します。

公開履歴の再監査:

```console
uv run python tools/public_history_scan.py --json output/public-history-scan.json
uv run python tools/public_history_scan.py --worktree --fail-on-findings
```

利用者固有の文字列は公開CIに置けないため、ローカルで照合します。`build`は`input/`の
**file名だけ**とWindows user名からSHA-256 patternを作り、ファイル内容は開きません。
patternと結果はGit対象外の`output/`へ置き、値は画面や報告JSONに出力されません。

```console
uv run python tools/private_history_match.py build \
  --input-dir input --output output/private-patterns.json
uv run python tools/private_history_match.py scan \
  --patterns output/private-patterns.json --json output/private-match.json
```

CIはpatternを必要としない汎用のemail形式、user絶対path、media拡張子検査だけを
現在treeに対して行います。固有値照合はpatternを持つ利用者がローカルで実行してください。

## Documentation

- [要件定義・設計](要件定義.md)
- [Release手順](docs/リリース手順.md)
- [Phase 3d統合受入結果](docs/受入試験統合結果_Phase3d.md)
- [Phase 4a公開履歴監査](docs/公開履歴監査_Phase4a.md)
- [Phase 4a照合走査](docs/照合走査_Phase4a.md)
- [変更履歴](変更履歴.md)

## ライセンスとモデル利用条件

utteranのcodeは[MIT License](LICENSE)です。依存library、driver、tool、modelには別のlicense・利用条件が
適用されます。特にgated modelはdownload前の同意が必要です。gatedでないmodelも、利用・再配布前に
model cardと配布元の最新条件を利用者自身で確認し、必要な同意・表示を行ってください。

詳細は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。この文書は法的助言では
ありません。
