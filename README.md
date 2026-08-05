# utteran

utteran は、音声・動画ファイルから話者を区別した文字起こしをローカルで生成する
Python 製 CLI ツールです。会議、インタビュー、講演の記録作成を主用途とし、音声を
クラウド文字起こし API へ送信しません。

Phase 2 では、単一ファイルとフォルダの逐次処理、段階別レジューム、モデル／ジョブ管理、
実行デバイス診断を提供します。文字起こしは faster-whisper、話者分離は
pyannote.audio 4.x、出力は SRT / VTT / JSON / TXT / Markdown に対応します。Phase 3aでは、
ハードウェアごとに独立した実行環境（venv）プロファイルと、whisper.cppのネイティブビルド
基盤を追加しました。

## 対応環境

- Windows 10/11（主対象）
- Linux（副対象。セットアップは下記の手動手順）
- Python 3.11 / 3.12
- CPU
- NVIDIA GPU（CUDA 12、cuDNN 9、cuBLAS、および対応ドライバーが必要）
- Intel GPU/NPU（`devices` で検出可能。OpenVINO ASR の実装は今後の対応）
- AMD GPU等（Vulkan経由。`vulkan` プロファイルでwhisper.cppのビルドのみ対応、実行は今後）

OpenVINO ASR、sherpa-onnx 話者分離、whisper.cppを使った実際の文字起こし、長時間音声の
分割処理は未実装です。Intel GPU/NPUがあっても`auto`は実装済みのfaster-whisper CPUを使用し、
将来の高速化候補を `devices` の警告に表示します。

## Windows セットアップ（プロファイル別 venv）

PyTorchはCPU版・CUDA版・XPU版が同一パッケージ名の別ビルドで、1つの仮想環境には1種類しか
導入できません。そのため utteran は**プロファイルごとに独立した venv**
（`.venvs\win-<profile>`）を持ちます。プロファイルを切り替えても他のプロファイルの
venvは変更されず、CUDA環境とIntel環境を同時に保持できます。

PowerShell でリポジトリ直下から `setup.ps1` を実行します。管理者権限は不要で、既存の
ffmpegと`.env`は再利用するため、繰り返し実行できます。モデル管理はセットアップから分離され、
通常実行中にモデルIDの入力待ちにはなりません。

```powershell
.\setup.ps1 -Profile cpu
.\setup.ps1 -Profile cuda
.\setup.ps1 -Profile intel
.\setup.ps1 -Profile vulkan
```

| Profile | 導入内容 | 想定環境 |
|---|---|---|
| `cpu` | faster-whisper、pyannote、CPU版PyTorch | GPUなし |
| `cuda` | faster-whisper、pyannote、CUDA 12.6版PyTorch。CUDA wheelは約2.4 GiB | NVIDIA |
| `intel` | faster-whisper、pyannote、XPU版PyTorch、OpenVINO、whisper.cppビルド用cmake | Intel CPU/Arc/NPU |
| `vulkan` | faster-whisper、pyannote（CPU版PyTorch）、whisper.cppビルド用cmake | AMD等（OpenVINOなし） |

プロファイル管理用の追加パラメーター:

```powershell
.\setup.ps1 -Profile cpu|cuda|intel|vulkan   # 作成または更新
             -SkipFfmpeg
             -VenvDir <パス>                 # venv配置場所の上書き
.\setup.ps1 -List                             # 作成済みプロファイルの一覧
.\setup.ps1 -Remove <profile> [-Yes]          # 指定プロファイルの削除
.\setup.ps1 -SetDefault <profile>             # 既定プロファイルの設定
```

`-Profile` はPythonとuvの確認、そのプロファイルだけの`uv sync`、ffmpeg、`.env`、
プロファイル別の検証（下記）の順で実行します。モデルの一覧・取得・削除は行わず、完了時に
専用コマンドを案内します。ネットワーク処理に失敗しても実行可能な項目を続け、残りの手順を
表示します。ffmpeg は公式ダウンロードページが案内する gyan.dev の
release essentials build を SHA-256 検証後、ユーザーデータ配下の `utteran/bin` に配置します。
この配布 build は GPLv3 です。バイナリはリポジトリには含まれません。

プロファイル別の検証内容:

- `cpu` / `cuda`: `devices --json` でfaster-whisper/pyannoteの実バックエンド初期化を確認
- `intel`: OpenVINOの初期化、torch XPUの検出（`torch.xpu.is_available()`）
- `vulkan`: Vulkanビルド前提（`glslc`）とランタイム（`vulkaninfo`）を個別に確認。
  一方だけが利用可能な場合があります

依存同期またはプロファイル別の実デバイス検証に失敗した場合、`setup.ps1` は不完全と表示して
終了コード1を返します。

WSLとWindowsから同じcheckoutを使う場合、venvディレクトリ名にOS識別子（`win-`/`linux-`）を
含むため、双方が独立して動作します。旧`.venv` / `.venv-windows`が存在する場合は変更・削除せず、
検出したことと手動削除の手順を表示します。

```console
Remove-Item -Recurse -Force .\.venv-windows   # 新方式の動作確認後、任意で実行
```

ディスク使用量の目安（`intel`プロファイルはxpu+openvino+whisper-cpp相当で約5 GiB、
実測値は導入パッケージの組み合わせで変動します。全プロファイルを作成すると6〜8 GiB程度）:

| プロファイル | venvサイズの目安 |
|---|---:|
| `cpu` | 約1.0 GiB |
| `cuda` | CUDA wheel込みで数GiB（CUDA extraは約2.4 GiB） |
| `intel` | 約4.9 GiB（xpu + openvino相当。pyannote込みでさらに増加） |
| `vulkan` | `cpu`相当 + cmake |

`.\setup.ps1 -List` で作成済みプロファイルの実サイズ・主要パッケージのバージョン・
最終更新日時を確認できます。

PowerShell の実行ポリシーで止まる場合は、現在のプロセスだけ許可して実行できます。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1 -Profile cpu
```

## `run.ps1`（プロファイル指定の実行）

セットアップ後は `run.ps1` でプロファイルの venv内の `utteran` を直接実行できます。

```powershell
.\run.ps1 transcribe .\input\a.mp4                    # 既定プロファイル
.\run.ps1 -Profile cuda transcribe .\input\a.mp4      # 明示指定
```

既定プロファイルは `config.toml` の `[general].default_profile`、未設定なら作成済み
プロファイルが1つの場合はそれを使用します。複数存在し既定も未設定の場合はエラーになるため、
`-Profile` を指定するか `.\setup.ps1 -SetDefault <profile>` を実行してください。
終了コードは `utteran` の終了コードをそのまま返します。

`uv run utteran ...` を直接使う場合は、対象プロファイルの `UV_PROJECT_ENVIRONMENT` を
設定します。

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'C:\path\to\Utteran\.venvs\win-cpu'
uv run utteran devices
```

## Windows対話フロント

セットアップ後は、リポジトリ直下で次を実行すると番号メニューだけで操作できます。

```powershell
.\start.ps1
```

音声・動画を `input` フォルダへ置き、文字起こしメニューでファイルまたはフォルダ一括を選びます。
既定の出力先は `output` です。両フォルダは空の状態でもGitに残りますが、利用者が置いた入力と
生成出力は拡張子を問わずGit対象外です。任意の入力／出力パスも指定できます。

文字起こしウィザードでは次を選択し、実行前に要約と実際のCLIコマンドを確認できます。

- ASR backend（auto / faster-whisper）とWhisper／Kotoba／任意の登録済みモデルまたはローカルパス
- auto / CPU / CUDAデバイス
- 日本語、英語、言語自動判定、任意の言語コード
- pyannote話者分離の有無、モデル、話者数の自動／固定／範囲指定
- SRT / VTT / JSON / TXT / Markdownの組み合わせ
- フォルダ再帰、include/exclude glob、resume/no-resume/force、lock解除、config、ログ詳細度
- 実行前のdry-run

メインメニューには使用中のプロファイルを表示し、モデルの一覧／取得／削除／検証、デバイス診断、
ジョブ管理、設定管理、input/outputフォルダをExplorerで開く操作を実行できます。
「プロファイル管理」メニューから、作成済みプロファイルの一覧表示、セッション内での切替、
新規作成／更新、既定プロファイルの設定、削除ができます。未実装のbackendは選択肢に
表示しません。入力したパスやglobはコマンド文字列として再評価せず、引数配列でCLIへ渡します。

## uv による手動インストール

[uv](https://docs.astral.sh/uv/) と Python 3.11 または 3.12 を用意し、リポジトリ直下で
必要な構成を同期します。`cpu`/`cuda`/`xpu` は同一venvへ同時導入できない排他extrasです。

文字起こしのみの軽量構成（話者分離なし、torch非依存）:

```console
uv sync
uv run utteran transcribe audio.wav --no-diarization
```

Windows setupと同じ明示profile相当を手動で選ぶ場合:

```console
uv sync --extra cpu
uv sync --extra cuda
uv sync --extra xpu --extra whisper-cpp --extra openvino   # intel profile相当
uv sync --extra cpu --extra whisper-cpp                    # vulkan profile相当
```

`cuda` はCUDA 12.6版PyTorchを使用し、仮想環境内のcuDNN/cuBLAS DLLを自動登録します。
GPUがCUDA wheelのcompute capabilityに非対応の場合は、メモリ確保だけでなく実CUDAカーネルの
実行・同期probeで不適合と判定します。`xpu`はtorchの推移的依存である`triton-xpu`をWindows限定で
同梱します（他プラットフォームのtorch+xpu組合せは未検証）。

`whisper-cpp`（`utteran native build`用のcmake）、`openvino`（OpenVINO GenAI相当。ASR自体は
Phase 3b以降）、`onnx`（将来のsherpa-onnx話者分離向けonnxruntime）は排他extrasと自由に
組み合わせられます。

開発ツールも導入する場合:

```console
uv sync --extra cpu --extra dev
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

トークンの参照元は環境変数、`.env`、OS キーリングの3段階で、この順に優先します。
一般設定の5段階（CLI、環境変数、`.env`、`config.toml`、既定値）とは別の秘密値専用経路です。
トークンを `config.toml` に
書かないでください。記載されていても値を無視して警告し、CLI、例外、コンソールログ、
ジョブログでは Hugging Face 形式および登録済みの秘密値をマスクします。

## モデル管理

数 GB の意図しない取得を防ぐため、モデルは暗黙にダウンロードしません。対話端末の
`transcribe` は未取得モデルの取得前に確認し、非対話環境では終了コード 3 で取得コマンドを
案内します。`--yes` は確認を省略して取得する明示的な指定です。

IDを省略すると、表示名、用途、backend、導入状態、概算サイズ、ライセンス、gated状態、正確な
IDを持つ番号付き一覧を表示します。番号またはIDをカンマ区切りで複数選択でき、Enterだけなら
何も取得せず終了します。

```console
uv run utteran models download
uv run utteran models list --available
uv run utteran models download faster-whisper:large-v3-turbo
uv run utteran models download faster-whisper:kotoba-whisper-v2.0
uv run utteran models download pyannote:pyannote/speaker-diarization-community-1
uv run utteran models verify
uv run utteran models remove faster-whisper:large-v3-turbo
uv run utteran models path
```

同じモデル名が複数 backend にあるため、一意な形式は `<backend>:<model-id>` です。backend と
モデルの組が一意なら非修飾 ID も使用できます。Kotoba-Whisperなど日本語向けの特殊用途モデルも
番号選択と明示ID指定の両方に対応します。パイプやCIなどの非対話環境ではID省略を拒否し、
意図しない取得を行いません。保存先は
`platformdirs.user_cache_dir("utteran") / "models"`、または `UTTERAN_MODEL_DIR` です。
Phase 1 と同様に Hugging Face の標準キャッシュも検出します。ローカルの CTranslate2／
pyannote ディレクトリを設定へ直接指定することもできます。

gated モデルの取得エラーは、トークン未設定、利用条件未同意／権限不足、無効トークンに分けて
対処先を表示します。

CTranslate2形式のモデルは、取得完了時と`models verify`実行時に、単語タイムスタンプ計算が
実際に動作するかを隔離subprocessで検証します。一部の第三者配布モデル（`kotoba-whisper-v2.0`など、
decoderを圧縮した蒸留系モデル）は、蒸留前の深いdecoder向けの`alignment_heads`設定を引き継いで
配布されており、単語タイムスタンプ計算時にネイティブクラッシュ（Pythonから捕捉不能な
プロセス終了）を起こします。この検証でクラッシュを検知した場合のみ、該当モデルの設定を
安全な既定値へ自動修正します（単語タイムスタンプの精度が下がる場合があります）。

`models list` は必須ファイルが欠けた部分取得を「不完全」と表示します。`models verify` は欠落した
ファイル名を報告し、同じIDで `models download` を実行すると既存ディレクトリを消さずに不足分の
取得を再開します。pyannote community-1の完全なスナップショットは現在約32 MiBで、設定ファイル
など約1 MiBだけの状態は正常な導入ではありません。WindowsではHugging Faceの長い一時ファイル名
が従来の260文字制限を超えても取得できるよう、ダウンロード時にextended-length pathを使用します。

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

処理完了時には、今回実行した音声抽出・正規化、文字起こし、話者分離、話者割当・結合、
出力生成の所要時間と実行フェーズ合計を`HH:MM:SS.mmm`形式で表示します。resumeで再利用した
フェーズは今回の合計に含めません。フォルダ一括処理では、成功した各ジョブの時間をフェーズ別に
合算して表示します。`start.ps1`から実行した場合も、出力ファイル一覧の後にこの集計が表示されます。

```console
uv run utteran transcribe meeting.mp4 --force
uv run utteran transcribe meeting.mp4 --format txt
```

フォルダ処理は1ファイルの失敗後も続行し、成功、スキップ、失敗と理由を最後に表示します。
一部失敗は終了コード 5、全ファイル失敗は 1、Ctrl+C は 130 です。モデルはバッチ全体で
一度だけロードします。デコード不能ファイルは失敗として集計し、完了済みジョブは理由付き
スキップになります。

終了コード:

| コード | 意味 |
|---|---|
| 0 | 正常終了 |
| 1 | 一般エラー、またはバッチ全件失敗 |
| 2 | 設定エラー（トークン未設定、不正値など） |
| 3 | 依存エラー（ffmpeg・モデル・backendなど） |
| 4 | 入力エラー（ファイル未検出、デコード失敗など） |
| 5 | バッチの一部失敗 |
| 130 | ユーザーによる中断 |

主な `transcribe` オプション:

```text
--format srt,vtt,json,txt,md
--output-dir PATH
--asr-backend auto|faster-whisper
--asr-model ID_OR_PATH
--diarization-backend pyannote
--diarization-model ID_OR_PATH
--asr-device auto|cpu|cuda|cuda:N|openvino|vulkan|openvino_vulkan
--diarization-device auto|cpu|cuda|cuda:N|xpu|xpu:N
--device DEVICE  # 両方へ同じ値を指定する従来互換オプション
--language CODE|auto
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
同期まで確認します。

`intel`プロファイルではPyTorch XPUも実カーネル実行まで確認し、利用できる場合は話者分離の
autoが`xpu:0`を選びます。優先順位はCUDA、XPU、CPUです。Arc内蔵GPUのメモリはシステムRAMとの
共有であり、長時間音声では通常のdGPU VRAM不足ではなくRAM不足として現れる場合があります。
XPUを明示する場合は`--device xpu`または`xpu:N`を使用し、他profileではintel profileの作成を
案内して拒否します。

現在のプロファイル名と作成済みの他プロファイル一覧（存在・最終更新のみ、他プロファイルの
Pythonは起動しません）、Vulkanのビルド前提（`glslc`）とランタイム（`vulkaninfo`）を区別した
検出結果、`utteran native build` によるネイティブビルドの状態（whisper.cppタグと各構成の
実行可否）も表示します。`--json` は将来の GUI から利用できる安定した構造化出力で、既存の
キー構造は保ったまま `profile` / `vulkan` / `native` を追加しています。

CLIから直接プロファイルの状態だけを確認する場合:

```console
uv run utteran profiles list       # 作成済みプロファイルと状態
uv run utteran profiles current    # 現在実行中のプロファイル（UTTERAN_PROFILE）
uv run utteran profiles path       # venv ルートパス
```

Pythonプロセス内でプロファイルを切り替えて再実行する機能はありません。切り替えは
`run.ps1` の責務です。

## whisper.cppバックエンドとネイティブビルド

`intel` / `vulkan` プロファイルでは、whisper.cppをソースから取得して複数構成でビルドできます。
ビルド後はGGMLモデルを取得し、`--asr-backend whisper-cpp`で文字起こしできます。

```console
uv run utteran native build                       # 前提を満たす全構成を試行
uv run utteran native build --variant cpu,vulkan  # 構成を絞る
uv run utteran native status                      # ビルド状態を表示
uv run utteran native clean --variant vulkan      # 1構成だけ削除
uv run utteran native clean --all                 # 全構成を削除
uv run utteran models list --available             # 推奨GGMLモデル
uv run utteran models list --available --all       # 英語専用を含む全GGMLモデル
uv run utteran models download whisper-cpp:large-v3-turbo-q5_0
uv run utteran transcribe input.wav --asr-backend whisper-cpp --asr-model large-v3-turbo-q5_0
```

ASRと話者分離では対応デバイスが異なります。特にwhisper.cppの
`openvino`／`vulkan`／`openvino_vulkan`はASR専用です。両方を使うIntel環境では、例えば
`--asr-device openvino_vulkan --diarization-device auto`と指定します。Windows対話フロントは
`devices --json`から両者を別々に提示するため、非対応のASR構成をpyannoteへ渡しません。
既存の`--device`は互換性のため残り、指定値を両方へ適用します。

| 構成 | 前提条件 |
|---|---|
| `cpu` | なし |
| `openvino` | `openvino` パッケージ、OpenVINO GPUの認識 |
| `vulkan` | Vulkan SDKの `glslc`（シェーダーコンパイラ） |
| `openvino_vulkan` | 上記両方（エンコーダをOpenVINO、デコーダをVulkanにオフロード） |

前提を満たさない構成は理由を記録してスキップし、要求した構成が1つもビルドできなかった場合
だけ終了コード3を返します。whisper.cppは `v1.9.1` に固定して取得します。ビルド成果物は
`~/.utteran/native`（`[general].native_dir` / `UTTERAN_NATIVE_DIR` で変更可）へ全プロファイル
共有で配置し、パス長対策のため構成ごとの内部ディレクトリ名は短縮しています
（例: `openvino_vulkan` → `ovvk`）。OpenVINOランタイムのDLLパスはビルド成果物に焼き込まず、
実行時にアクティブな環境から動的に解決します。**Vulkanのビルドは長時間かかることがあります**
（特にシェーダー生成）。進捗が表示されますが、応答がないように見える時間帯があります。

`[asr].word_timestamps`は`auto`（話者分離時だけ、既定）/`always`/`never`です。単語時刻を
取得する場合はDTWのためflash attentionを無効化するので低速になります。whisper.cppは
サブプロセス方式のため、バッチでもファイルごとにGGMLモデルをロードします。

OpenVINO構成では、GGMLとは別に最大約3GBのOpenAI PyTorch重みを一時取得してencoder IRを
準備します。IRはモデルサイズごとに1組で、q5_0など量子化違いにも共有されます。

```console
uv run utteran models prepare-openvino whisper-cpp:large-v3-turbo-q5_0 --device GPU
uv run utteran models list-openvino
uv run utteran models remove-openvino whisper-cpp:large-v3-turbo-q5_0
```

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

## 実測性能の目安

### Phase 3b Intel実機（whisper.cpp）

Windows 11、Intel Core Ultra 7 255H、Intel Arc 140T iGPU、large-v3-turbo-q5_0、
180秒の合成日本語音声でのend-to-end時間です。単語TSありはDTWのためflash attentionを
無効化しています。

| 構成 | 単語TSあり | 単語TSなし |
|---|---:|---:|
| whisper-cpp / CPU | 463.352秒 | 391.030秒 |
| whisper-cpp / OpenVINO | 62.719秒 | 57.674秒 |
| whisper-cpp / Vulkan | 40.863秒 | 30.699秒 |
| whisper-cpp / OpenVINO+Vulkan | 33.585秒 | 26.421秒 |
| faster-whisper / CPU | 178.344秒 | 非対応（既存経路は常時取得） |

OpenVINO+Vulkanが最速で、単語TSなしはありより約21%高速でした。CPU auto fallbackを
faster-whisperのままにする判断とも整合します。

Phase 3受入試験では実会議から内容を記録せず生成した180秒fixtureで再測定しました。

| 構成 | 単語TSあり | 単語TSなし |
|---|---:|---:|
| whisper-cpp / CPU | 207.425秒 | 171.427秒 |
| whisper-cpp / OpenVINO | 36.328秒 | 33.939秒 |
| whisper-cpp / Vulkan | 22.340秒 | 17.194秒 |
| whisper-cpp / OpenVINO+Vulkan | 23.734秒 | 18.403秒 |
| faster-whisper / CPU | 90.826秒 | 非対応（既存経路は常時取得） |

**上記2つの表はPhase 3dのハルシネーション対策（`no_context = true`等）適用前の測定値です。
現在はこの後の「Phase 3dのハルシネーション対策とbenchmark」節に記載した値へ改善しています。**
ただしwhisper-cliへ渡すコマンドライン引数自体は対策前後で変化していないため、差は主に
測定時点のばらつき（熱・電源状態等）によるものです。

同じ3分の複数話者fixtureに対するpyannoteはCPU 106.681秒、XPU 41.953秒でした。
process treeのピークworking setは、ASRを含む独立測定でCPU 8.02 GiB、XPU 8.18 GiBです。
XPUはGPU専用メモリではなく共有system RAMを使うため、他processを含む空きRAMに注意してください。

### Phase 1/2 NVIDIA実機

Windows 11、GTX 1070 Ti 8 GiB、8 physical / 16 logical core CPU、large-v3-turbo/int8、
pyannote community-1での受入試験値です。音声内容、モデル、他processのGPU使用量で変動します。

| 音声長 | 構成 | 処理時間 | 実時間比 | ピークRAM |
|---:|---|---:|---:|---:|
| 3分 | CPU、ASRのみ | 104.688秒 | 1.720x | 1.85 GiB |
| 3分 | CUDA、ASRのみ | 20.844秒 | 8.635x | 2.12 GiB |
| 3分 | CUDA、ASR＋話者分離 | 35.859秒 | 5.020x | 2.14 GiB |
| 約2時間19分 | CUDA、ASRのみ | 417.734秒 | 20.02x | 7.38 GiB |
| 約2時間19分 | CUDA、ASR＋話者分離 | 770.625秒 | 10.85x | 7.37 GiB |

詳しい条件、VRAM、モデルロード時間、CPU話者分離値は
[受入試験報告](docs/受入試験報告.md)を参照してください。

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

## Phase 3dのハルシネーション対策とbenchmark

whisper.cppでは無音時の反復を抑えるため、既定で`no_context = true`（`--max-context 0`）を
使います。entropy/logprob/no-speech閾値と温度fallbackも設定可能です。Silero GGML VADは
`vad = true`で有効です。VADモデルは
`utteran models download whisper-cpp-vad:silero-v6.2.0`で取得でき、`vad_model`未指定時は管理済み
モデルを自動解決します。同一segment抑制は根本対策後の保険として`repetition_limit = 10`、0なら
無効です。抑制件数はwarningに残り、正当な相槌も除外し得ます。

24分46秒の実会議WAV、Vulkan、large-v3-turbo-q5_0、反復抑制無効で個別測定した結果、対策なしは
最大150連続（343反復segment、289.94秒）でした。前文脈遮断のみは最大3（3、110.50秒）、VADのみは
最大2（2、103.58秒）、entropy 2.4のみは最大31（90、193.25秒）、logprob -1.0のみとno-speech
0.6のみは最大150で単独効果なしでした。既定の前文脈遮断＋decoder閾値では最大3です。VADは有効
ですが出力segment構成を大きく変え、追加モデルも必要なため既定では無効のままとします。

```console
uv run utteran benchmark --audio sample.wav --variants vulkan,openvino_vulkan --json benchmark.json
uv run utteran benchmark --audio sample.wav --variants vulkan,openvino_vulkan --apply
```

実データを暗黙利用しないためWAVは必須です。既定でwarmup 1回・計測3回の中央値を表示し、認識
本文やジョブは保存しません。

**反復対策適用後の再測定**（Intel Core Ultra 7 255H / Arc 140T、large-v3-turbo-q5_0、180秒WAV、
warmup 1・3回中央値）:

| 構成 | TSなし | TSあり |
|---|---:|---:|
| whisper-cpp / CPU | 148.460秒（1.213x） | 183.135秒（0.983x） |
| whisper-cpp / OpenVINO | 30.587秒（5.886x） | 34.138秒（5.273x） |
| whisper-cpp / Vulkan | 15.363秒（11.718x） | 22.310秒（8.069x） |
| whisper-cpp / OpenVINO+Vulkan | 19.638秒（9.167x） | 24.086秒（7.474x） |
| faster-whisper / CPU | 79.551秒（2.263x） | 74.672秒（2.411x） |

VAD有効時のVulkan（TSなし）は14.322秒（12.570x）で、無効時（15.363秒）と有意差はなく、
VAD自体はほぼ処理時間へ影響しません。180秒fixtureではVulkanがOpenVINO+Vulkanより高速という
順序は対策前と変わりませんでした（本機では約28%高速）。IR変換にはOpenAI PyTorch重み
（モデルサイズにより最大約3 GB）の追加取得と変換が必要です。

**しかし、24分46秒の実会議WAV（単語TSなし、1点測定）ではこの順序が逆転します。**

| 構成 | 実時間比 | 秒 |
|---|---:|---:|
| whisper-cpp / Vulkan | 13.420x | 110.730秒 |
| whisper-cpp / OpenVINO+Vulkan | 22.811x | 65.141秒 |

180秒の結果から音声長の比（約8.26倍）で単純に外挿するとVulkanは約127秒、ovvkは約162秒の
見込みですが、実測はVulkan 110.7秒・ovvk 65.1秒で、**ovvkがこの実音声ではVulkanより
約41%高速**という、短い素材からは予測できない逆転が生じました。原因はOpenVINOエンコーダの
初期化コストが短い音声で相対的に大きく、長い音声で償却されるためと考えられますが、
単一環境・単一素材の1点測定であり断定はしていません。

**IRを用意しなくてもVulkanで十分な場合がある一方、長時間の会議録音ではIR変換済みの
OpenVINO+Vulkanが大きく有利な場合があります。** どちらが有利かは音声長・hardware・model
依存です。**自環境・自分の音声長では`utteran benchmark`で判断してください。**
本機のautoは、IR変換なしでも確実に動作する`vulkan`を既定で優先しますが
（`models prepare-openvino`未実行の環境で`auto`が初期化に失敗するのを避けるため）、
30分〜3時間の会議録音を扱う場合はIR変換済みなら`[asr.whisper_cpp].variant = "openvino_vulkan"`
を明示指定することを推奨します。

同じ実音声の連結測定では、Vulkan ASRの25/50/100分が102.95/220.69/443.09秒、ピークRAM
1.47/1.74/2.28 GBでした。XPU話者分離は212.52/401.55/811.92秒、5.41/5.58/6.10 GBで、
100分までOOMはなくRAM増加は緩やかでした。CPU話者分離25/50分は655.91/1128.59秒、
2.75/2.91 GBです。

## 統合受入試験ハーネス

Phase 1〜3の受入試験は`tools/acceptance/harness.py`と`cases.json`へ統合されています。環境を
`devices --json`/`models list --json`から判定し、利用不能なCUDA等は理由付きskip、環境を変える
ケースは既定除外、結果はJSONLとサマリーJSONへ出力します。Python APIの`run_selected()`からも
呼び出せます。実行方法、スキーマ、復元手順、手動確認項目は`tools/acceptance/README.md`を参照。

2026-08-05にIntel実機で長時間・破壊的ケースを含む統合192ケースを実行し、ID別最新結果は
177合格・15 CUDA理由付きskip・0失敗でした（P系77/77合格）。詳細は
`docs/受入試験統合結果_Phase3d.md`に記録しています。
150分は時間制約から省略し、100分傾向から120分約6.3 GB、150分約6.6 GBと外挿します。本機の
68 GB RAMでは2時間級に十分な余裕が見込まれますが、共有RAM使用量と他processの負荷に注意して
ください。この値をメモリ容量・driverの異なる環境へそのまま一般化しないでください。

uvプロファイルのPythonは3.12.13です。システムPython 3.14.6を対応版として使っているわけでは
なく、パッケージの対応範囲は引き続き3.11/3.12です。

utteran のコードは [MIT License](LICENSE) です。Whisper、Kotoba Whisper、PyTorch、NVIDIA
CUDAライブラリ、pyannote、OpenVINO 配布モデル、ffmpeg などには個別のライセンスと利用条件があります。特に pyannote
community-1 は CC-BY-4.0 で、利用前にモデル利用条件への同意が必要です。Windows setup が
取得する gyan.dev の FFmpeg essentials build は GPLv3 です。詳細は
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) と各モデルページを確認してください。
