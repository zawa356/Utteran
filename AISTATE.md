# AI 作業状態

## 現在のフェーズと進捗

- Phase 1（骨格と最小動作）: 実装完了、ローカル検証完了（gated pyannote 実モデル E2E を除く）。
- Phase 1 初回コミット: `83a4b29 feat: implement Phase 1 transcription pipeline`。
- Phase 2（実運用機能）: 実装完了、ローカル検証完了。ジョブ／レジューム、フォルダバッチ、
  devices、モデル／ジョブ／設定管理 CLI、Windows setup を実装。Windows 実機 setup と
  gated pyannote／既定 large-v3-turbo／実 CUDA 推論は環境制約により未検証として継続管理する。
- `docs/utteran_Phase2_指示書.md` 全399行、既存状態、要件定義、変更履歴を読了し、
  コード着手前の指定仕様訂正5点を要件定義へ反映済み。
- `docs/utteran_設計書.md` 全715行を読了。
- コード着手前の必須4文書を作成。

## 実装計画

### Phase 1（完了）

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

### Phase 2（完了）

1. `jobs.py` に入力識別、決定的 config hash、原子的 manifest、中間 JSON、ロックを実装し、
   モデル不要テストで破損・再開・依存無効化を検証する。
2. pipeline を永続ジョブ上の段階実行へ移し、バックエンドをバッチ全体で再利用できる
   ライフサイクル、進捗、キャンセル、force/resume/export-only を結合する。
3. 安定した名前順のフォルダ選別と逐次バッチ処理、include/exclude、集計、終了コードを実装する。
4. `devices.py` に注入可能な環境検出と実 CUDA 可用性に基づく自動判定を実装し、CLI を追加する。
5. モデルカタログ／管理層を実装し、明示的な取得・削除・検証と gated エラー分類を CLI に接続する。
6. `jobs` / `config` サブコマンドと確認プロンプト、JSON／人間向け表示を追加する。
7. 冪等な Windows `setup.ps1` を作成し、静的検査可能範囲と未検証事項を記録する。
8. README と必須文書を各作業単位で同期し、ruff、mypy、モデル不要 pytest、CLI 結合確認を行う。

## 直近の作業内容と結果

- Phase 2 着手時に指定された仕様訂正を反映。`job_id` は入力ハッシュのみ、設定差分は
  ステージ別 config hash で判定し、決定的 JSON 正規化を要求する仕様へ変更した。
- バッチ一部失敗の終了コード 5、全滅時 1、PID/開始時刻を持つジョブロックと
  `--force-unlock`、`devices --json` を要件定義へ追加した。
- `jobs.py` に bounded input fingerprint、入力だけに基づく job ID、決定的 stage config hash、
  原子的 manifest／schema version 付き中間 JSON、依存グラフによる無効化を実装した。
- PID と開始時刻を持つ排他ロック、生存 PID の拒否、古い／破損ロックの回収、ジョブ一覧、
  削除候補選択、サイズ集計を実装。ジョブ／型テスト 12 passed、mypy 25 files success。
- pipeline をジョブの5段階へ移行。正規化 WAV と schema version 付き ASR／話者分離／merge
  結果を永続化し、同一設定の再実行は全段階をスキップ、出力設定だけの変更は export だけを再実行する。
- `BackendPool` で ASR／話者分離モデルを設定キーごとに一度だけロードし、逐次バッチ全体で
  再利用する。直接注入した fake backend を使う Phase 1 テスト互換も維持した。
- `batch.py` に既知メディア拡張子の事前選別、再帰、include/exclude、安定名前順、dry-run、
  個別失敗継続、成功／スキップ／失敗集計と終了コード 0/1/5 を実装した。
- pipeline/batch/jobs の重点テスト 18 passed、ruff 対象検査 passed、mypy 26 files success。
- `devices.py` に CPU topology/AVX、CTranslate2 CUDA compute types、NVIDIA 名称/VRAM、
  cuDNN/cuBLAS、PyTorch 実 CUDA 確保、OpenVINO、ONNX Runtime、ffmpeg の独立検出を実装。
- `DeviceProbeSet` で検出関数を注入可能にし、JSON 化可能な `DeviceReport` と auto 選択を追加。
  デバイス／ASR／pyannote 重点テスト 9 passed、ruff passed、mypy 27 files success。
- faster-whisper は CUDA の対応 compute type を検証し、auto では float16 → int8_float16 →
  int8 の順で選択。明示デバイス／compute type は不適合時にエラーとし、退避しない。
- `models/catalog.py` に Phase 2 最低要件5エントリを backend 別に登録し、同名モデルを
  `<backend>:<model-id>` で一意表示する。`models/manager.py` に保存先、検出、明示取得、削除、
  必須ファイルとサイズの検証、進捗／キャンセル境界、Hub エラー分類を実装した。
- 管理領域を優先し、Phase 1 と整合する Hugging Face 標準キャッシュも local-only で検出する。
  backend load は管理済み snapshot をローカルパスとして解決し、暗黙取得を維持しない。
- models／faster-whisper／pyannote の重点テスト 12 passed、ruff passed、mypy 30 files success。
- Typer CLI に transcribe のバッチ／resume／force／lock／dry-run／yes と、devices、models、jobs、
  config の全 Phase 2 サブコマンドを接続した。削除前表示・確認、非対話モデル取得拒否、
  部分失敗 exit 5、JSON raw 出力、秘密登録／マスクを実装した。
- CLI/config 13 tests passed。全 src/tests ruff passed、mypy 30 files success。
- 実環境 `utteran devices --json` exit 0。CPU 16 logical/8 physical、AVX2、GTX 1070 Ti 8GiB、
  CTranslate2 4.8.1 は CUDAを1台列挙するが cuDNN/cuBLAS未解決のため usable=false、auto=CPU/int8。
  PyTorch/OpenVINO/ffmpeg は現 dev extra 環境で未導入、ONNX Runtime CPU/Azure は検出。
- Windows `setup.ps1` を作成。cpu/cuda/intel profile、SkipModels/SkipFfmpeg/ModelDir/Models、
  uv sync、SHA-256 検証付き ffmpeg 配置、.env 非上書き、モデル事前取得、CUDA/全devices 診断を実装。
- Windows PowerShell 5.1 Parser API を WSL から実行し `PowerShell syntax OK`。実際の sync、
  ネットワーク取得、ffmpeg 展開、CUDA DLL 解決は Windows 実機では未実行。
- `intel` extra に openvino-genai 2025以上2027未満を追加し、`uv lock` は159 packagesで成功。
- README を全 Phase 2 CLI、resume/config hash、バッチ集計、Windows setup、Linux 手動導入、
  モデル／ジョブ保存先と削除、devices JSON、ライセンスへ同期。THIRD_PARTY_NOTICES も更新。
- job ごとの `utteran.log` handler を追加し、JSON Lines と最終 formatter の秘密マスクで段階遷移を
  記録。managed pyannote の token なし local 解決順序と、再帰バッチの job/output 自己入力を修正。
- quiet 時もジョブログには INFO の段階遷移を残し、コンソールだけを抑制するよう handler を分離。
  破損 manifest のジョブも `corrupt` として一覧・削除対象にできるよう復旧経路を補強した。
- 一時 Linux 静的 ffmpeg、合成 MP4、cached faster-whisper tiny、話者分離なしで Phase 2 実 E2E。
  初回は5段階、同条件の2回目は全段階 skip、形式変更は export-only、force は全段階再実行を確認。
- 最終検査: Python 3.12.3 でモデル不要 72 tests passed、ruff check／src・tests format check、
  mypy strict（30 source files）、`uv lock --check`（159 packages）がすべて成功。
- Python 3.11.15 の隔離 dev 環境でもモデル不要 72 tests passed。
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
- `setup.ps1` は Windows PowerShell 5.1 Parser API による構文検査済みだが、Windows 実機での
  uv sync、ネットワーク取得、ffmpeg 展開、CUDA DLL 解決を含む一気通貫実行は未実施。

## 設計上の判断とその理由

- config hash の浮動小数固定桁は12桁とした。仕様は固定桁化のみを要求し桁数を未指定だが、
  設定値の実用精度を十分保持しつつ、浮動小数の表現揺れを排除できるため。
- 破損または未対応バージョンの manifest は `manifest.corrupt.<日時>.json` へ退避して警告し、
  新規 manifest から全段階を再計算する。破損データを黙って上書きせず復旧を継続するため。
- manifest の各ステージへ `error` と `artifacts` を追加した。6.4 の必須項目を保ちつつ、
  jobs 表示で失敗理由を示し、export 出力の消失をレジューム判定できるようにするため。
- Phase 2 は OpenVINO ASR 実装を明示的に除外しているため、Intel GPU/NPU を検出しても現在の
  `auto` は実装済み faster-whisper CPU を選ぶ。devices では Phase 3 の高速化候補として警告する。
  未実装 backend を「実際に使われる」と表示して transcribe を失敗させないため。
- CTranslate2 の「実推論可能」判定はモデル非依存で可能な
  `get_supported_compute_types(device, index)` によるランタイム初期化を一次判定とし、モデル load を
  最終判定とする。モデルを暗黙取得せずに任意 GPU で完全な推論 probe は構成できないため。
- モデルカタログの取得元は公式 Hugging Face ページと faster-whisper 1.2.1 の alias 表で確認し、
  turbo=`mobiuslabsgmbh/faster-whisper-large-v3-turbo`、large-v3=`Systran/...`、
  Kotoba=`kotoba-tech/kotoba-whisper-v2.0-faster`、OpenVINO=`OpenVINO/...fp16-ov` とした。
- 同じ `large-v3-turbo` が複数 backend にあるため、CLI の一意キーは
  `<backend>:<model-id>` とし、transcribe は backend と model_id の組で曖昧なく解決する。
  backend なしの曖昧な指定は、誤った大容量形式を取得しないよう候補を示して拒否する。
- Windows ffmpeg は ffmpeg.org が公式ダウンロードページで案内する gyan.dev の
  `ffmpeg-release-essentials.zip` を採用した。配布物はGPLv3のため取得前に表示し、リポジトリへ
  同梱せず、公開 `.sha256` と照合してからユーザーデータ配下へ配置する。
- setup の cpu/cuda profile は既定話者分離を利用できるよう pyannote extra を含め、intel は
  pyannote + intel extras とした。profile はハードウェア選択であり ASRだけのlite導入ではないため。
- `models remove` は utteran 管理コピーを優先し、標準 HF cache のみ存在する場合は
  huggingface_hub の cache API で当該 repo の revision を削除する。明示削除要求を満たしつつ、
  パスを推測した再帰削除を避けるため。
- Phase 2 指示の export hash 表は `[output]` のみだが、`[general].output_dir` を変えた際に旧出力を
  誤って再利用するため、実効出力先の絶対パスも hash 対象へ追加し要件定義を同期した。
  出力場所だけの変更なので export-only 再実行となり、上流キャッシュは維持される。
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
- Phase 1 時点の `auto` は CTranslate2 の CUDA 列挙だけを見る最小選択だったが、Phase 2 で
  compute type と CUDA ライブラリを含む実可用性判定へ置き換えた。
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
- Phase 1 では正規化 WAV を `TemporaryDirectory` に置いていたが、Phase 2 で schema version 付き
  中間 JSON とともにジョブディレクトリへ永続化し、段階レジュームへ利用するよう置き換えた。
- pyannote のトークン不足は ASR 前に検出するため、レジストリに軽量 preflight を置いた。
  pipeline は固有実装を import せず、不要な音声抽出・ASR 後に失敗することを防ぐ。
- バックエンド由来の生例外文は、トークンを含む可能性を完全には否定できないため、
  ユーザー向け例外へ埋め込まない。verbose ログも最終 formatter でマスクする。

## 次に着手すべきこと

- Windows 実機が利用可能になったら `setup.ps1` の cpu/cuda/intel profile、ffmpeg 取得、
  オフライン継続、冪等な再実行を一気通貫で確認する。
- HF 利用条件へ同意済みのトークンと対応ランタイムが得られれば community-1、
  large-v3-turbo、実 CUDA の未検証 E2E を実施する。
- Phase 2 の変更一式は `feat: implement Phase 2 operational workflows` としてコミットする。

## 既知の落とし穴・回避方法

- バックエンド固有オブジェクトを pipeline/exporter に渡さず、共通 dataclass へ変換する。
- Hugging Face トークンは config.toml から無視し、ログと例外をマスクする。
- pyannote.audio 4.x の出力 API の差異をバックエンド内部で吸収する。

## 動作確認環境・手順

- 作業パス: `/mnt/c/UserDataFile/Git/Utteran`
- OS 実行環境: Linux/WSL 系 bash（詳細確認は今後実施）
- Git リポジトリ初回コミットは `83a4b29`。Phase 2 実装と指示書は
  `feat: implement Phase 2 operational workflows` の変更セットに収録。
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
- Python 3.12.3: `uv run --no-sync pytest -m "not requires_model"` = 72 passed。
- Python 3.11.15 隔離環境: `uv run --isolated --python 3.11 --extra dev pytest -m
  "not requires_model"` = 72 passed（Python 3.11.15）。
- `uv run --no-sync ruff check`: All checks passed。
- `uv run --no-sync ruff format --check src tests`: 44 files already formatted。
- `uv run --no-sync mypy`: Success、30 source files。
- `uv run utteran --help` / `transcribe --help`、devices、models、jobs、config の主要 read-only
  コマンド: exit 0。
- 合成 MP4 + Linux ffmpeg: 正規化結果 mono / sample width 2 / 16kHz を確認。
- 合成 MP4 + cached faster-whisper tiny + device auto: CLI exit 0、CPU fallback、
  SRT/VTT/JSON/TXT/MD の5ファイルと JSON schema_version 1 を確認。
- Phase 2 実 E2E: 一時 Linux 静的 ffmpeg + 合成 MP4 + cached tiny + no-diarization。
  初回5段階実行、同一 job ID の2回目全 skip、format 変更時 export-only、force 時全段階実行を確認。
- `utteran devices --json`: exit 0。実環境の CPU／CTranslate2 CUDA 列挙／CUDA ライブラリ不足／
  ONNX Runtime／ffmpeg 不在を JSON 化し、auto=CPU/int8 を確認。
- Windows PowerShell 5.1 Parser API: `setup.ps1` は `PowerShell syntax OK`。
- `uv lock --check`: 成功、159 packages 解決済み。
- `git diff --check`: 問題なし。
- `要件定義.md` は Phase 1 設計書を基礎に、Phase 2 指示書の訂正5点と実効 output_dir の
  export hash 判断を同期済み。このため設計書原本との差分は意図した仕様更新。
