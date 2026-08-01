# utteran

utteran は、音声・動画ファイルから話者を区別した文字起こしをローカルで生成する
Python 製 CLI ツールです。会議、インタビュー、講演の記録作成を主用途とし、音声を
クラウド文字起こし API へ送信しません。

Phase 1 では、単一ファイルの音声正規化、faster-whisper による文字起こし、
pyannote.audio 4.x による話者分離、話者割当、5形式の出力を提供します。

## 対応環境

- Windows 10/11（主対象）
- Linux（副対象）
- Python 3.11 / 3.12
- CPU
- NVIDIA GPU（CUDA。faster-whisper で使う場合は対応する NVIDIA ドライバー、CUDA、
  cuDNN、cuBLAS が必要）

Intel GPU/NPU、OpenVINO、sherpa-onnx、AMD 向けバックエンド、フォルダ一括処理、
ジョブのレジューム、長時間音声の分割処理は後続フェーズの対象です。

## インストール

パッケージ管理には [uv](https://docs.astral.sh/uv/) を使用します。Python 3.11 または
3.12 を用意し、リポジトリ直下で同期してください。

文字起こしのみの軽量構成（faster-whisper、話者分離なし）:

```console
uv sync
uv run utteran transcribe audio.wav --no-diarization
```

pyannote.audio と PyTorch を含む推奨構成:

```console
uv sync --extra pyannote
```

開発ツールも導入する場合:

```console
uv sync --extra pyannote --extra dev
```

インストール後は `uv run utteran ...`、または有効化した仮想環境内で `utteran ...` を
実行できます。

## モデルの準備

数 GB の意図しない取得を防ぐため、Phase 1 はモデルを暗黙にダウンロードしません。
ネットワーク接続できる環境で、使用前にモデルを Hugging Face キャッシュへ取得してください。

既定の faster-whisper `large-v3-turbo`:

```console
uv run hf download mobiuslabsgmbh/faster-whisper-large-v3-turbo
```

話者分離の既定モデル:

```console
uv run hf download pyannote/speaker-diarization-community-1
```

ASR は `--asr-model` へ CTranslate2 モデルのローカルディレクトリを指定することもできます。
pyannote のローカルディレクトリは `config.toml` の `[diarization].model` で指定します。

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
`.env.example` を `.env` にコピーして `HF_TOKEN` を設定する方法、または次の OS キーリングも
利用できます。

```console
uv run keyring set utteran huggingface
```

トークンの優先順位は環境変数、`.env`、OS キーリングです。トークンを `config.toml` に
書かないでください。記載されていても utteran は値を無視して警告し、ログでは
Hugging Face 形式および認識済みの秘密値をマスクします。

## ffmpeg の準備

[ffmpeg](https://ffmpeg.org/download.html) を別途インストールしてください。バイナリは
本リポジトリに同梱しません。

- Windows: 公式ダウンロードページから配布元を選び、`ffmpeg.exe` を `PATH` に追加する。
- Debian / Ubuntu: `sudo apt install ffmpeg`
- その他の Linux: ディストリビューションのパッケージ管理機能で導入する。

utteran は次の順で実行ファイルを探索します。

1. `config.toml` の `[ffmpeg].path`
2. `PATH`
3. `platformdirs.user_data_dir("utteran")/bin/ffmpeg.exe` または `ffmpeg`

見つからない場合やデコードに失敗した場合は、対処方法を含むエラーを返します。入力音声は
ffmpeg で 16 kHz / mono / PCM 16bit WAV に正規化されます。

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

主なオプション:

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
--config PATH
--verbose
--quiet
```

`--num-speakers` と `--min-speakers` / `--max-speakers` は同時指定できません。既定の出力先は
`./output` です。既存ファイルと衝突した場合は `_1`、`_2` のような連番を付けます。

## 設定

既定の `config.toml` は `platformdirs.user_config_dir("utteran")` に置きます。別のファイルは
`--config` で指定できます。設定の優先順位は次のとおりです。

```text
CLI 引数 > 環境変数 > .env > config.toml > 既定値
```

環境変数は `UTTERAN_<SECTION>__<KEY>` 形式です。例:

```console
export UTTERAN_ASR__LANGUAGE=en
export UTTERAN_DIARIZATION__NUM_SPEAKERS=2
```

設定項目の全体と既定値は [要件定義.md](要件定義.md) の5章および7章を参照してください。

## 対応形式

- 音声: wav, mp3, m4a, flac, ogg, aac, wma
- 動画: mp4, mkv, mov, avi, webm, ts
- 出力: SRT (`.srt`)、WebVTT (`.vtt`)、JSON (`.json`)、テキスト (`.txt`)、
  Markdown (`.md`)

ffmpeg でデコード可能な形式を受け付けます。上記は明示的なサポート対象です。JSON は
`schema_version: 1` と単語タイムスタンプを含み、中間結果の再利用に適した形式です。

## 開発と検査

```console
uv sync --extra dev
uv run ruff check src tests
uv run mypy src
uv run pytest -m "not requires_model"
```

実モデルを必要とするテストは `requires_model` マーカーで分離します。

## ライセンス

utteran のコードは [MIT License](LICENSE) です。Whisper、pyannote のモデル、ffmpeg などには
それぞれ別のライセンスと利用条件があります。特に pyannote community-1 は CC-BY-4.0 で
提供され、利用前にモデル利用条件への同意が必要です。詳細は
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
