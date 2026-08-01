# AI 作業状態

## 現在のフェーズと進捗

- Phase 1（骨格と最小動作）: 実装完了、ローカル検証完了（gated pyannote 実モデル E2E を除く）。
- Phase 2 着手前の初回 Git スナップショットを作成する作業単位。追跡対象の棚卸しと
  `.gitignore` 整備を完了し、Phase 1 一式を1コミットにまとめる。
- `docs/utteran_設計書.md` 全715行を読了。
- コード着手前の必須4文書を作成。

## 実装計画

1. プロジェクトメタデータ、src レイアウト、ライセンス、依存ライセンス表記を作成する。
2. 共通型、例外、設定、ログの基盤を実装し、モデル不要テストを追加する。
3. ffmpeg の探索、入力検証、音声抽出・正規化を実装してテストする。
4. 抽象基底を介した faster-whisper ASR バックエンドを実装する。
5. 抽象基底を介した pyannote.audio 4.x 話者分離バックエンドを実装する。
6. 設計書7章の突き合わせアルゴリズムを実装し、境界ケースをテストする。
7. SRT、VTT、JSON、TXT、Markdown exporter を実装し、書式をテストする。
8. 単一ファイル pipeline と `transcribe` CLI を結合し、エラー終了コードをテストする。
9. `uv sync`、ruff、mypy、モデル不要 pytest、可能な範囲の ffmpeg 結合確認を実施する。
10. 4文書を実装結果と検証結果に合わせて最終更新する。

## 直近の作業内容と結果

- `pyproject.toml`、src パッケージ骨格、MIT ライセンス、依存物の注意書き、
  `.gitignore`、`.env.example` を作成した。
- ローカル環境は Python 3.12.3。uv 0.12.1 をユーザー領域へ導入。WSL 側のシステム
  ffmpeg は未導入だが、一時 Linux 静的 ffmpeg で実デコードを検証済み。
- `types.py`、`errors.py`、`config.py`、`logging.py` と対応する単体テストを作成した。
- `audio.py` に ffmpeg 探索、16 kHz mono PCM16 変換、進捗通知、協調キャンセルを実装した。
  一時ファイル成功後の置換により、失敗時に既存出力を破壊しない。
- `ASRBackend`、レジストリ、`FasterWhisperBackend` を実装。バックエンド固有型は内部で
  共通 dataclass へ変換する。モデル不要の fake model テストを追加。
- `uv 0.12.1` をユーザー領域に導入し、`uv sync --extra dev` 成功。初期テスト 12件合格。
- pyannote.audio 4.0.7 の実 API を確認し、通常区間と exclusive 区間を共通型へ変換する
  `PyannoteBackend` を実装。fake pipeline によるモデル不要テストを追加。
- `uv sync --extra pyannote --extra dev --link-mode=copy` 成功（89パッケージ、NTFS コピー約9分）。
- 設計書7章の割当、分割、極短話者島吸収、同一話者結合、登場順リナンバーを
  `align.py` の純粋処理として実装し、境界テストを追加。
- SRT/VTT/JSON/TXT/Markdown の exporter、共通レジストリ、表示名置換、BOM/改行、
  衝突回避を実装し、モデル不要テストを追加。
- 単一ファイル pipeline と Typer CLI を実装。レジストリ経由のバックエンド生成、
  `--num-speakers`、`--no-diarization`、5形式指定、Rich 進捗、終了コードを結合した。
- 初回全体テストは 28 passed / 2 CLI failed。Typer 単一コマンド自動昇格を callback で抑止し、
  Rich の狭幅折返しを許容するテストへ修正。全 CLI エラーに秘密値マスクを追加した。
- README を完成済み Phase 1 の機能、導入、モデル事前取得、トークン、ffmpeg、CLI、設定、
  対応形式、検査、ライセンスに合わせて全面更新した。
- extra なし `uv sync --check` 成功（49 packages）。続いて dev extra に戻し、pyannote 未導入の
  軽量環境でも 31 tests / ruff / mypy が成功した。
- 一時導入した Linux 静的 ffmpeg で合成 MP4 を正規化し、mono / PCM16 / 16kHz を実測した。
  Windows ffmpeg.exe は WSL パス非変換のため直接結合には使えなかった。
- `hf download Systran/faster-whisper-tiny` 後、合成 MP4 を実モデル CPU ASR に通し、
  `--no-diarization --format srt,vtt,json,txt,md` が exit 0、5ファイル生成、JSON schema 1 を確認。
- 現環境は CTranslate2 が CUDA 1台を報告する一方 float16 非対応だった。`auto` の CUDA 初期化が
  失敗した場合のみ CPU/int8 へフォールバックするよう修正。明示的 CUDA はエラーのままにする。
- uv 管理 Python 3.11.15 の隔離 dev 環境でモデル不要 35 tests passed。
- WSL の `TEMP=/mnt/c/...` では pytest 既定 fd capture の匿名一時ファイルが失われるため、
  `addopts` に `--capture=sys` を設定。指定どおり引数なしで安定して走るようにした。
- Phase 1 コミット前に追跡対象を棚卸し。リポジトリ内に音声・動画・字幕・ログの残存なし。
  `.env` は内容を読まず ignore を確認し、`.venv` も追跡対象外とした。設計書と Phase 1 指示書は
  仕様・作業履歴として追跡対象に維持した。

## 未解決の課題・保留事項

- pyannote 実モデル E2E は HF トークン未設定かつ gated モデル未取得のため未実施。
  `uv sync --extra pyannote`、pyannote.audio 4.0.7 API 確認、fake pipeline 結合は実施済み。
- faster-whisper は tiny/CPU の実モデル E2E 済み。既定 large-v3-turbo と実 CUDA 推論は
  モデル／CUDA ランタイム未導入のため未実施。
- 設計書はキーリングの service/user 名を規定していないため、実装判断を下記へ記録済み。
- Phase 1 はジョブ管理を含まないため、設計原則の中間ファイル永続化は Phase 2 の責務とし、Phase 1 では結果モデルの JSON シリアライズ性を保証する。

## 設計上の判断とその理由

- `keyring` はコア依存にした。HF トークン探索は Phase 1 の通常機能であり、利用環境で
  常に同じ優先順位を保証するため。キーリング自体が利用不能な場合は安全にスキップする。
- 依存バージョンは互換性のあるメジャーバージョン範囲で制約した。再現性は `uv.lock` で担保し、
  パッチ更新を許容するため。
- CLI 上書き値は `Config.load(cli_overrides=...)` のネスト辞書として渡す。pydantic-settings の
  env / dotenv source と TOML を低優先度から deep merge し、指定の優先順位を明示した。
- HF トークン名は `HF_TOKEN` を優先し、互換用に `HUGGING_FACE_HUB_TOKEN` も受け付ける。
  キーリングの service/user は設計書に指定がないため `utteran` / `huggingface` とした。
- 設計書7.2の「全閾値を設定可能」に対応するため、例示 TOML にはない `[alignment]` 設定を追加。
- 進捗コールバックは `ProgressEvent` 1引数、キャンセルは thread-safe な `CancelToken` とした。
  GUI と CLI の双方で扱いやすく、バックエンドシグネチャを安定させるため。
- ffmpeg は拡張子で入力を拒否せず、存在する通常ファイルを ffmpeg に渡して判定する。
  設計書の「ffmpeg でデコードできるものはすべて受け付ける」方針を優先した。
- ffmpeg 出力は同一ディレクトリの一時ファイルに生成後 `Path.replace` する。デコード失敗時に
  既存ファイルを半端な内容で上書きしないため。
- Phase 1 のモデル管理除外と設計書10.1の「暗黙ダウンロード禁止」を両立するため、
  faster-whisper は `local_files_only=True` で Hub キャッシュを参照し、ローカルパスは直接読む。
- Phase 2 の詳細デバイス検出は実装せず、Phase 1 の `auto` は CTranslate2 が CUDA を報告すれば
  `cuda:0`、それ以外は CPU とする最小選択に限定した。
- pyannote の Hub ID は `snapshot_download(..., local_files_only=True)` で既存キャッシュのみを
  解決する。未取得時だけ軽量な model_info で未同意／無効トークンを分類し、暗黙取得しない。
- pyannote 4.x/TorchCodec は ffmpeg 共有ライブラリを別途要求するため、ffmpeg で作った
  16 kHz mono PCM16 WAV を `wave` + torch で読み、メモリ上の waveform として渡す。
  これにより実行ファイル探索と共有ライブラリ探索の不一致を回避する。
- 「極端に短いセグメントの吸収」は詳細な片側規則が未規定のため、前後の話者が同じ場合に
  中央の短い話者島を両隣へ吸収する、と解釈した。相槌によるラベル細分化防止に直結し、
  異なる話者の正当な短い発話を端部や片側だけで消さないため。
- 話者区間の包含は半開区間 `[start, end)`、同順位は開始時刻→終了時刻→ラベル順とした。
  区間境界と完全同率の結果をプラットフォーム間で決定的にするため。
- 連番ファイル名は設計書に記法の指定がないため `<stem>_1.<ext>` とした。複数形式の一部だけ
  番号がずれないよう、要求された全形式で空いている共通 stem を選ぶ。
- SRT/VTT の話者接頭辞は設計書に具体表記がないため `表示名: 本文` とした。TXT と統一され、
  汎用字幕ソフトでもプレーンテキストとして表示できるため。
- Phase 1 は jobs/レジューム対象外のため、正規化 WAV は `TemporaryDirectory` に置き、重い結果は
  JSON シリアライズ可能な共通モデルで保持する。永続化は Phase 2 のジョブ層で追加する。
- pyannote のトークン不足は ASR 前に検出するため、レジストリに軽量 preflight を置いた。
  pipeline は固有実装を import せず、不要な音声抽出・ASR 後に失敗することを防ぐ。
- バックエンド由来の生例外文は、トークンを含む可能性を完全には否定できないため、
  ユーザー向け例外へ埋め込まない。verbose ログも最終 formatter でマスクする。

## 次に着手すべきこと

- HF 利用条件へ同意済みのトークンがある環境で community-1 を事前取得し、実話者音声による
  pyannote E2E と `--num-speakers` の実モデル精度確認を行う。
- Phase 1 スナップショット後、Phase 2（ジョブ管理・レジューム、フォルダ処理、モデル管理、
  setup.ps1、devices）へ進む。

## 既知の落とし穴・回避方法

- バックエンド固有オブジェクトを pipeline/exporter に渡さず、共通 dataclass へ変換する。
- Hugging Face トークンは config.toml から無視し、ログと例外をマスクする。
- pyannote.audio 4.x の出力 API の差異をバックエンド内部で吸収する。

## 動作確認環境・手順

- 作業パス: `/mnt/c/UserDataFile/Git/Utteran`
- OS 実行環境: Linux/WSL 系 bash（詳細確認は今後実施）
- 現在のディレクトリは Git リポジトリとして初期化されていない。
- `python3 --version`: 3.12.3。
- `/home/<user>/.local/bin/uv --version`: 0.12.1。
- WSL の `ffmpeg -version`: 未導入。Windows 側 `C:\path\ffmpeg\bin\ffmpeg.exe` は確認したが、
  WSL パス引数との相互運用制約があるため製品検証には一時 Linux 静的版を使用。
- `/home/<user>/.local/bin/uv sync --extra dev`: 成功、59パッケージ導入、`uv.lock` 生成。
- `uv run pytest -m 'not requires_model' --capture=sys`: 基盤段階で 12 passed。
- pyannote.audio 4.0.7 の `Pipeline.from_pretrained` / `DiarizeOutput` を導入済みコードで確認。
- 現環境はシステム ffmpeg 共有ライブラリ未導入のため TorchCodec が警告する。waveform 渡しで回避。
- align 追加時: 7 tests passed、ruff passed、mypy strict passed（外部 typed package は追跡除外）。
- `uv sync --extra pyannote --extra dev --link-mode=copy`: 成功、pyannote.audio 4.0.7 導入確認。
- `uv sync --link-mode=copy` と直後の `uv sync --check`: 成功（extra なし49 packages）。
- 最終環境は `uv sync --extra dev --link-mode=copy` 済み（pyannote extra は現在未導入）。
- Python 3.12.3: `uv run pytest -m "not requires_model"` = 36 passed（最終）。
- Python 3.11.15 隔離環境: 同テスト = 35 passed（トークン優先テスト追加前）。
- `uv run ruff check`: All checks passed。
- `uv run mypy`: Success、24 source files。
- `uv run utteran --help` / `uv run utteran transcribe --help`: exit 0、transcribe のみ表示。
- 合成 MP4 + Linux ffmpeg: 正規化結果 mono / sample width 2 / 16kHz を確認。
- 合成 MP4 + cached faster-whisper tiny + device auto: CLI exit 0、CPU fallback、
  SRT/VTT/JSON/TXT/MD の5ファイルと JSON schema_version 1 を確認。
- `uv lock --check`: 成功、156 packages 解決済み。
- `diff -q docs/utteran_設計書.md 要件定義.md`: 差分なし。
