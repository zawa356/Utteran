# utteran

utteran は、音声・動画ファイルから話者を区別した文字起こしをローカルで生成する
Python 製 CLI ツールです。会議、インタビュー、講演の記録作成を主用途とし、音声を
クラウド文字起こし API へ送信しません。

Phase 2 では、単一ファイルとフォルダの逐次処理、段階別レジューム、モデル／ジョブ管理、
実行デバイス診断を提供します。文字起こしは faster-whisper、話者分離は
pyannote.audio 4.x、出力は SRT / VTT / JSON / TXT / Markdown に対応します。

## 対応環境

- Windows 10/11（主対象）
- Linux（副対象。セットアップは下記の手動手順）
- Python 3.11 / 3.12
- CPU
- NVIDIA GPU（CUDA 12、cuDNN 9、cuBLAS、および対応ドライバーが必要）
- Intel GPU/NPU（`devices` で検出可能。OpenVINO ASR の実装は Phase 3）

OpenVINO ASR、sherpa-onnx 話者分離、AMD 向け推論、長時間音声の分割処理は未実装です。
Intel GPU/NPU があっても Phase 2 の `auto` は実装済みの faster-whisper CPU を使用し、
将来の高速化候補を `devices` の警告に表示します。

## Windows セットアップ

PowerShell でリポジトリ直下から `setup.ps1` を実行します。管理者権限は不要で、既存の
ffmpeg、`.env`、モデルは再利用するため、繰り返し実行できます。

```powershell
.\setup.ps1 -Profile cpu
.\setup.ps1 -Profile cuda
.\setup.ps1 -Profile intel
```

| Profile | 導入内容 |
|---|---|
| `cpu` | faster-whisper、pyannote、CPU版PyTorch |
| `cuda` | faster-whisper、pyannote、CUDA 12.6版PyTorch。CUDA wheelは約2.4 GiB |
| `intel` | `cpu`相当とOpenVINO GenAI。Phase 2では検出のみ |

主なパラメーター:

```powershell
.\setup.ps1 -Profile cpu|cuda|intel
             -SkipModels
             -SkipFfmpeg
             -ModelDir C:\path\to\models
             -Models faster-whisper:large-v3-turbo,pyannote:pyannote/speaker-diarization-community-1
```

スクリプトは Python と uv の確認、profile 別 `uv sync`、ffmpeg、`.env`、モデル取得、
CUDA DLL、`utteran devices` の順で確認します。ネットワーク処理に失敗しても実行可能な項目を
続け、残りの手順を表示します。ffmpeg は公式ダウンロードページが案内する gyan.dev の
release essentials build を SHA-256 検証後、ユーザーデータ配下の `utteran/bin` に配置します。
この配布 build は GPLv3 です。バイナリはリポジトリには含まれません。

WSLとWindowsから同じcheckoutを使い、`.venv` がLinux用だった場合、スクリプトは既存環境を
変更せず `.venv-windows` を使用します。実行したPowerShellではその環境が選択されます。
新しいPowerShellで `uv run` を使う場合は、スクリプト末尾に表示されるとおり設定します。

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'C:\path\to\Utteran\.venv-windows'
uv run utteran devices
```

依存同期または選択profileの実デバイスprobeに失敗した場合、後続のモデル／devices処理を
重ねて実行せず、`setup.ps1` は不完全と表示して終了コード1を返します。

PowerShell の実行ポリシーで止まる場合は、現在のプロセスだけ許可して実行できます。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -Profile cpu
```

## uv による手動インストール

[uv](https://docs.astral.sh/uv/) と Python 3.11 または 3.12 を用意し、リポジトリ直下で
必要な構成を同期します。

文字起こしのみの軽量構成（話者分離なし）:

```console
uv sync
uv run utteran transcribe audio.wav --no-diarization
```

pyannote.audio とCPU版PyTorchを含む構成:

```console
uv sync --extra pyannote
```

Windows setupと同じ明示profileを手動で選ぶ場合:

```console
uv sync --extra cpu
uv sync --extra cuda
uv sync --extra intel
```

`cpu`、`cuda`、`intel` は相互に切り替えて使用します。`cuda` はCUDA 12.6版PyTorchを使用し、
仮想環境内のcuDNN/cuBLAS DLLを自動登録します。GPUがCUDA wheelのcompute capabilityに
非対応の場合は、メモリ確保だけでなく実CUDAカーネルの実行・同期probeで不適合と判定します。

Intel runtime の検出も有効にする構成（OpenVINO ASR 自体は Phase 3）:

```console
uv sync --extra pyannote --extra intel
```

開発ツールも導入する場合:

```console
uv sync --extra pyannote --extra dev
```

Linux では上記 `uv sync` を実行し、ディストリビューションのパッケージ管理機能で ffmpeg を
導入してください。インストール後は `uv run utteran ...`、または有効化した仮想環境内で
`utteran ...` を実行できます。

## Hugging Face の準備

`pyannote/speaker-diarization-community-1` には、Hugging Face のアクセストークンと
モデル利用条件への同意が必要です。

1. [Hugging Face](https://huggingface.co/) のアカウントを作成する。
2. [community-1 のモデルページ](https://huggingface.co/pyannote/speaker-diarization-community-1)
   を開き、利用条件に同意する。
3. [Access Tokens](https://huggingface.co/settings/tokens) で読み取り権限のトークンを発行する。
4. 次のいずれかにトークンを保存する。

環境変数:

```console
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
```

PowerShell では `$env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxxxxx"` を使用します。リポジトリ直下の
`.env.example` を `.env` にコピーして設定する方法、または OS キーリングも利用できます。

```console
uv run keyring set utteran huggingface
```

トークンの優先順位は環境変数、`.env`、OS キーリングです。トークンを `config.toml` に
書かないでください。記載されていても値を無視して警告し、CLI、例外、コンソールログ、
ジョブログでは Hugging Face 形式および登録済みの秘密値をマスクします。

## モデル管理

数 GB の意図しない取得を防ぐため、モデルは暗黙にダウンロードしません。対話端末の
`transcribe` は未取得モデルの取得前に確認し、非対話環境では終了コード 3 で取得コマンドを
案内します。`--yes` は確認を省略して取得する明示的な指定です。

```console
uv run utteran models list --available
uv run utteran models download faster-whisper:large-v3-turbo
uv run utteran models download pyannote:pyannote/speaker-diarization-community-1
uv run utteran models verify
uv run utteran models remove faster-whisper:large-v3-turbo
uv run utteran models path
```

同じモデル名が複数 backend にあるため、一意な形式は `<backend>:<model-id>` です。backend と
モデルの組が一意なら非修飾 ID も使用できます。保存先は
`platformdirs.user_cache_dir("utteran") / "models"`、または `UTTERAN_MODEL_DIR` です。
Phase 1 と同様に Hugging Face の標準キャッシュも検出します。ローカルの CTranslate2／
pyannote ディレクトリを設定へ直接指定することもできます。

gated モデルの取得エラーは、トークン未設定、利用条件未同意／権限不足、無効トークンに分けて
対処先を表示します。

## ffmpeg の準備

[ffmpeg](https://ffmpeg.org/download.html) を別途用意してください。バイナリは本リポジトリに
同梱しません。

- Windows: `setup.ps1`、または公式ページが案内する配布元から取得して `PATH` に追加する。
- Debian / Ubuntu: `sudo apt install ffmpeg`
- その他の Linux: ディストリビューションのパッケージ管理機能で導入する。

utteran は `[ffmpeg].path`、`PATH`、ユーザーデータ配下の `utteran/bin` の順で探索します。
入力音声は 16 kHz / mono / PCM 16bit WAV に正規化し、ジョブ内へ永続化します。

## 基本的な使い方

既定形式（SRT、JSON、Markdown）で話者付き文字起こしを作成:

```console
uv run utteran transcribe meeting.mp4
```

5形式を出力し、話者数を2人に固定:

```console
uv run utteran transcribe interview.wav --format srt,vtt,json,txt,md --num-speakers 2
```

話者分離を省略:

```console
uv run utteran transcribe lecture.m4a --no-diarization
```

フォルダ直下を名前順に逐次処理:

```console
uv run utteran transcribe recordings/
```

再帰処理と glob 選別:

```console
uv run utteran transcribe recordings/ --recursive \
  --include "**/*.wav" --include "**/*.mp4" --exclude "**/draft-*"
```

対象だけを確認:

```console
uv run utteran transcribe recordings/ --recursive --dry-run
```

既定でレジュームは有効です。2回目は完了済みステージをスキップし、出力形式、話者表示名、
出力先だけの変更は export のみ再実行します。全段階をやり直す場合は `--force`、レジュームを
使わない一回だけの実行は `--no-resume` を指定します。同一ジョブが live PID でロック中なら
拒否し、プロセスが存在しない古いロックは自動回収します。所有状況を確認したうえで解除する
場合だけ `--force-unlock` を使用してください。

```console
uv run utteran transcribe meeting.mp4 --force
uv run utteran transcribe meeting.mp4 --format txt
```

フォルダ処理は1ファイルの失敗後も続行し、成功、スキップ、失敗と理由を最後に表示します。
一部失敗は終了コード 5、全ファイル失敗は 1、Ctrl+C は 130 です。モデルはバッチ全体で
一度だけロードします。デコード不能ファイルと完了済みジョブは理由付きスキップになります。

主な `transcribe` オプション:

```text
--format srt,vtt,json,txt,md
--output-dir PATH
--asr-backend auto|faster-whisper
--asr-model ID_OR_PATH
--diarization-backend pyannote
--device auto|cpu|cuda|cuda:N
--language CODE
--num-speakers N
--min-speakers N
--max-speakers N
--no-diarization
--recursive
--include GLOB / --exclude GLOB
--resume / --no-resume
--force / --force-unlock
--dry-run / --yes
--config PATH
--verbose / --quiet
```

`--num-speakers` と `--min-speakers` / `--max-speakers` は同時指定できません。既定の出力先は
`./output` です。既存ファイルと衝突した場合は `_1`、`_2` のような連番を付けます。

## ジョブとレジューム

既定のジョブ保存先は `platformdirs.user_cache_dir("utteran") / "jobs"` で、
`[general].job_dir` から変更できます。入力ごとに、manifest、正規化 WAV、ASR、話者分離、
merge の schema version 付き JSON を保存します。入力内容から同じ16文字 job ID を再利用し、
ステージ別 config hash で影響箇所以降だけを再計算します。

```console
uv run utteran jobs list
uv run utteran jobs show a1b2c3d4e5f6a7b8
uv run utteran jobs clean --failed
uv run utteran jobs clean --older-than 30
uv run utteran jobs clean --all --yes
```

削除前に対象を表示して確認します。`--yes` で確認を省略できます。manifest は各遷移で
一時ファイルから原子的に置換し、Ctrl+C で中断した段階は次回 pending から再開します。

## デバイス診断

```console
uv run utteran devices
uv run utteran devices --json
```

CPU コア／AVX、CTranslate2 CUDA GPU／VRAM／compute type、cuDNN/cuBLAS、PyTorch CUDA、
OpenVINO devices、ONNX Runtime providers、ffmpeg と backend 導入状況を表示します。末尾には
現在の `auto` が実際に選ぶ ASR／話者分離 backend、device、compute type とフォールバック理由を
表示します。Windowsでは物理コア数とAVXをWin32 APIで取得し、PyTorch CUDAは実カーネル実行と
同期まで確認します。`--json` は将来の GUI から利用できる安定した構造化出力です。

## 設定

既定の `config.toml` は `platformdirs.user_config_dir("utteran")` に置きます。

```console
uv run utteran config path
uv run utteran config init
uv run utteran config show
```

`config init` はトークンを含まない雛形を作り、既存ファイルを上書きしません。`config show` は
トークン源を含まない検証済みの実効設定を JSON で表示します。別の設定ファイルは
`transcribe --config`、または管理コマンドの `--config` / `--path` で指定できます。

設定の優先順位:

```text
CLI 引数 > 環境変数 > .env > config.toml > 既定値
```

環境変数は `UTTERAN_<SECTION>__<KEY>` 形式です。

```console
export UTTERAN_ASR__LANGUAGE=en
export UTTERAN_DIARIZATION__NUM_SPEAKERS=2
```

全設定と既定値は [要件定義.md](要件定義.md) の5章および7章を参照してください。

## 対応形式

- 音声: wav, mp3, m4a, flac, ogg, aac, wma
- 動画: mp4, mkv, mov, avi, webm, ts
- 出力: SRT (`.srt`)、WebVTT (`.vtt`)、JSON (`.json`)、テキスト (`.txt`)、
  Markdown (`.md`)

単一ファイルは拡張子にかかわらず ffmpeg でデコードを試みます。フォルダ処理は効率のため上記
拡張子を既定候補とし、`--include` で追加できます。JSON 出力は `schema_version: 1` と単語
タイムスタンプを含みます。

## 開発と検査

```console
uv sync --extra dev
uv run ruff check src tests
uv run mypy
uv run pytest -m "not requires_model"
uv lock --check
```

実モデルを必要とするテストは `requires_model` マーカーで分離します。

## ライセンス

utteran のコードは [MIT License](LICENSE) です。Whisper、Kotoba Whisper、PyTorch、NVIDIA
CUDAライブラリ、pyannote、OpenVINO 配布モデル、ffmpeg などには個別のライセンスと利用条件があります。特に pyannote
community-1 は CC-BY-4.0 で、利用前にモデル利用条件への同意が必要です。Windows setup が
取得する gyan.dev の FFmpeg essentials build は GPLv3 です。詳細は
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) と各モデルページを確認してください。
