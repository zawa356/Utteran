# AI 作業状態

## 現在のフェーズと進捗

- Phase 1（骨格と最小動作）: 実装完了、ローカル検証完了（gated pyannote 実モデル E2E を除く）。
- Phase 1 初回コミット: `83a4b29 feat: implement Phase 1 transcription pipeline`。
- Phase 2 実装コミット: `54fa46d feat: implement Phase 2 operational workflows`。
- Phase 2（実運用機能）: 実装完了、ローカル検証完了。ジョブ／レジューム、フォルダバッチ、
  devices、モデル／ジョブ／設定管理 CLI、Windows setup を実装。Windows実機でcpu/cuda/intelを
  検証済み。gated pyannote／既定 large-v3-turboの実モデルE2Eは未検証として継続管理する。
- Phase 2 follow-up（モデル導線改善）: 実装・Windows/WSL検証完了。setupからモデル管理を分離し、
  models CLIへ説明付き一覧、番号選択、従来の明示ID指定を集約した。
- Phase 2 follow-up（Windows対話フロント）: 実装・Windows/WSL検証完了。input/output運用と、
  start.ps1からtranscribe・models・devices・jobs・config・setupへ到達する番号メニューを実装した。
- Phase 2 follow-up（不完全モデル検出）: 実装・Windows/WSL検証完了。利用者環境でpyannote
  community-1の重みが欠落した約1.1 MiBの部分取得を「導入済み」「正常」と誤判定する問題と、
  Windows MAX_PATHによる取得失敗を修正した。
- Phase 1/2受入試験: 進行中。`docs/utteran_受入試験指示書.md`全534行と必須4文書を読了し、
  開始コミット`57ecbb3`から専用branch`test/acceptance-phase2`を作成。開始時の未追跡指示書を
  `8b55d5d chore: snapshot before acceptance testing`で記録した。
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

### Phase 2 follow-up（完了）

1. 今回の利用者指示によるsetup／models責務変更を要件定義へ反映する。
2. setupからモデル関連パラメータと対話取得を除去し、専用CLIの案内だけを表示する。
3. モデルカタログへ人間向け表示名と用途を追加し、models listを分かりやすくする。
4. `models download` のID省略時に、複数選択可能な番号／IDプロンプトを実装する。
5. 対話／非対話／明示IDの回帰テスト、Windows setup実機試験、全品質検査を行う。
6. README、変更履歴、AISTATEを同期し、問題がなければコミットする。

### Phase 2 follow-up / Windows対話フロント（完了）

1. start.ps1、input/output、言語autoの追加仕様を要件定義へ反映する。
2. input/outputの内容を除外しつつ、各 `.gitkeep` を追跡する。
3. 実装済みbackendだけを使う文字起こしウィザードをPowerShell引数配列で構築する。
4. models/devices/jobs/config/setupとフォルダを開く管理メニューを接続する。
5. PowerShell Parser、メニュー終了、dry-runコマンド構築、Python品質ゲートを検証する。
6. README、変更履歴、AISTATEを同期し、コミット可能な差分として整える。

### Phase 2 follow-up / 不完全モデル検出（完了）

1. 利用者のpyannote保存先とHugging Face tree metadataを読み取り、期待ファイルと実体を比較する。
2. 完全／部分取得の候補を区別し、形式別必須ファイルを検証して欠落名を返す。
3. 部分取得を一覧・verify・remove・download再開へ接続し、回帰テストを追加する。
4. カタログ概算サイズと必須文書を実態へ同期する。
5. WSL／Windows品質ゲートと実キャッシュ表示を検証し、コミットする。

### Phase 1/2受入試験（進行中）

1. Windows環境・入力メタデータを本文非参照で記録し、派生クリップをoutput配下へ生成する。
2. 再開・個別再実行・timeout・process tree停止・出力抄録・peak memory対応ハーネスを作る。
3. G1〜G12を順番に実行し、各groupのresults/report/AISTATEをコミットする。
4. 不具合は失敗結果のtest commit後、回帰テスト・文書とともにfix commitし、再試験する。
5. 機能試験完了後だけcudaへ切替え、G13の2時間耐久試験を行う。
6. G14で集計、秘密／Git混入検査、profile判断、必須文書と最終報告を完了する。

## 直近の作業内容と結果

- 受入開始環境: Windows 11 Pro 10.0.26200、Python 3.12.0、uv 0.11.32、cpu profile、
  8 physical/16 logical cores、RAM 51,462,012,928 bytes、Cドライブ空き90,454,274,048 bytes。
  GTX 1070 Ti 8 GiBは列挙されるがcpu profileではauto=CPU/int8。devices JSON exit 0。
- 実会議MP4はファイル名・サイズ・ffprobe metadataだけを取得。377,645,510 bytes、8,363.883秒、
  H.264 1920x1080 60fps + AAC 48kHz stereo。原本は未変更で、本文は表示していない。
- `tools/acceptance`へ再開可能ハーネス、統計／構造validator、音量統計で3分区間を選ぶ素材生成、
  受入専用token-free configとG1 case定義を追加。結果全文を永続化せずedge/error行だけ記録する。
- 実素材の6候補を8kHz monoの1秒RMSで比較し、start=4,600.136秒（active ratio=0.850、
  variation=0.572、score=0.907）を選択。30.084秒MP4、180.084秒MP4/WAV/M4A、600.084秒MP4、
  broken/empty/notmediaとbatch fixtureをoutput/_testdataへ生成。原本は未変更、全生成物ignore確認済み。
- `docs/受入試験報告.md`を作成し、開始環境・commit・branchを記録。文字起こし本文は記載しない。
- G1-01/02合格。30秒MP4をCPU/int8、話者分離なし、language autoで49.312秒、5形式exit 0。
  validatorはduration=30.016秒、segments=5、empty=0、coverage=0.851、日本語文字比率=0.981、
  duplicate max=1、schema/SRT/VTT/UTF-8/単語時刻を合格判定。本文は永続記録していない。
- G2-01〜16は全件合格。3分MP4/WAV/M4A、既定／5形式、別output-dir、衝突連番、CRLF、
  SRT BOM、language ja/autoを検証。3媒体のduration=180.003〜180.011秒、segments=38〜44、
  認識文字数=693〜723で同等性閾値内、auto判定はja。CPU/int8実推論103.782〜115.328秒、
  process tree peak=1,882,664,960〜2,091,962,368 bytes。本文は記録していない。
- G2のCRLF用configにASR設定がなく、初回は出力だけの変更にもかかわらずASRを再実行した。
  製品のresume判定ではなく受入fixtureの差分が原因。CRLF/BOM用configへ基準と同じCPU/int8 ASR
  設定を追加し、BOM試験はexport-only 1.171秒で完了した。
- G3初回は15件中G3-11のみ合格、14件失敗。実pyannoteの全実行がexit 3で、後続validatorは
  成果物不足。原因U-001はpyannote 4.xの`setup_hook`がcallbackを`file=`キーワード付きで呼ぶ一方、
  `PyannoteBackend.diarize`内部hookの引数名が`_file`でTypeErrorになること。hookなしで同一モデル、
  正規化PCM、CPUを直接処理すると正常終了したため切り分け済み。指示書どおり失敗結果を先に
  test commitし、その後hook互換修正・モデル不要回帰・G3再試験を行う。
- U-001修正: 内部hookの引数名をpyannote 4.xと同じ`file`へ変更し、fake pipelineも`file=`を
  渡す回帰に更新。PCM waveformを直接渡して内蔵デコードを使わないため、pyannote import時の
  TorchCodec不在警告だけを限定的に抑制した。品質ゲート後にG3全ケースをrerunする。
- U-001修正後のG3全15ケースは合格。auto=内部4話者／出力3話者、固定2/3/4は中間結果が指定どおり、
  range 2〜5は4話者。通常70 turnsに11 overlap pairs、exclusive 61 turnsは非重複、平均2.278秒、
  dominant ratio=0.543、UNKNOWN=0。CPU diarizationは105.203〜108.093秒、peak process tree
  2,601,222,144〜2,671,165,440 bytes。label変更はexport-only 1.172秒。次はG4。
- G4-01〜08合格。全skip、export-only、ASR以降、diarization以降、force/no-resume全5段階を
  manifest record差分で確認。large-v3 ASR変更188.453秒、話者数変更108.172秒、force
  317.719秒、no-resume 319.188秒。
- G4-09は10分ASRへWindows CTRL_BREAK_EVENT送信後、audio done/asr pendingは保持したが子exit=1で
  失敗。G4-10はaudioを再利用しasr以降を281.906秒で再開完了。G4-11では同じcontrol eventが
  harnessへ伝播してKeyboardInterrupt終了（H-002）。残存processなし、10分jobにstale `.lock`あり。
  G4-11失敗をresultsへ手動追記。失敗結果をtest commit後、子を独立consoleへ置きhelper自身だけ
  control eventを無視する方式へ修正し、G4-09/11から再試験する。G4-12〜16は未実施。
- H-002修正: 試験対象を`CREATE_NEW_CONSOLE`で起動し、scenario processを元consoleから切り離して
  子consoleへAttach、送信側だけCtrl+C無視にして`GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)`を
  送る方式へ変更。親harnessへイベントを伝播させず実際のCtrl+Cを模擬するWindows限定回帰を追加。
- H-002の独立console最小probeはchild exit130/parent aliveで実機合格。修正後G4-09は、asr stage
  startから8秒（`Processing audio`ログ直前）にCtrl+C送信後60秒応答せずhelperがkillし再失敗。
  U-002を確定。raw manifestはaudio done/asr runningだがJobManifest読込時にpendingへ回収される。
  この失敗を先にtest commit後、実推論開始ログを待つscenario改善と製品のsignal/cancel経路を調査。
- U-002修正: run_pipeline/run_batchをdaemon workerで実行し、main threadは100ms周期で完了待ち。
  KeyboardInterrupt時はCancelTokenを設定し、最大1秒だけ協調終了を待ってCancelledError(exit130)へ
  変換する。ネイティブ呼出しが戻らなくてもCLI process終了でworkerを停止し、manifest runningと
  stale lockは既存の再開処理が回収する。通常結果と`_thread.interrupt_main`のモデル不要回帰を追加。
- U-002初回fix後の実再試験ではmainは割り込みを受けたが、CPython shutdownがnative側の残存threadを
  待ち60秒で終了しなかった。実CLI wrapperに限りエラー表示をflushして`os._exit(130)`するよう補強。
  raw running manifestはscenarioがJobStore.openでpendingへ回収できることを確認し、hard-exitは別process
  のモデル不要回帰でreturn code 130を検証する。
- hard-exit追加後のG4-09再実行は、前回kill由来のraw `asr=running`をscenarioが即座に今回の開始と
  誤認して早期送信し、再度timeout。worker/hard-exit自体はWindows実console probeで130を再確認。
  scenarioは実行前manifest mtime/log sizeを記録し、今回のmanifest更新＋新しい`Processing audio`
  ログを確認後にだけCtrl+Cを送るよう修正した。
- fresh ASRログ待ち後も`utteran.exe`だけはsignal未応答だが、同一コードの`python -m utteran.cli`は
  exit130/audio done/asr pendingで合格。実process treeはdistlib `utteran.exe`→venv python.exe→
  system python.exeの3段で、子PythonがCtrl+C ignore状態を継承していた。`transcribe`開始時に
  Win32 `SetConsoleCtrlHandler(NULL, FALSE)`でignore flagを解除し既存割り込み経路へ接続する。
- launcher Ctrl+C復元後のG4-09は20.375秒で合格（exit130/audio done/asr pending）。G4-10再resumeも
  asr以降を282.938秒で完走。U-002解消。
- G4-11はstale lock誤認を補助で除いた後、競合側が`予期しないエラー: <built-in function kill>
  returned a result with an exception set`でexit1。原因U-003はWindowsの`os.kill(pid, 0)`生存probe。
  失敗をtest commit後、Win32 OpenProcess/GetExitCodeProcessの非破壊probeへ置換しWindows回帰追加。
- U-003修正: `_process_exists`はWindowsだけ`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`と
  `GetExitCodeProcess(STILL_ACTIVE)`を使用。access deniedは安全側でlive、無効handleはstale扱い。
  Windows限定で実sleep childのlive/terminatedを確認するモデル不要回帰を追加。G4-11再試験待ち。
- G4最終は最新16ケース全件合格。G4-09 exit130/audio done/asr pending=20.375秒、G4-10 resume
  asr以降=282.938秒、G4-11 contender exit1/owner exit130=3.922秒。stale/live force unlock、corrupt、
  jobs list/show/clean全selector、242-line JSON job log parse/token-patternなしも合格。U-002/U-003解消。
  G4性能peak: large-v3 ASR変更3.55GB、pyannote変更2.69GB、force/no-resume各4.42GB、10m resume
  2.08GB。次はG5 batch。
- G5-01初回は219.812秒で正常3件を継続完了したが、先頭broken.mp4をAudioDecodeError=skippedとし
  process exit0だったため、期待するpartial failure exit5に不合格（U-004）。Phase 2要件の
  「デコード不可はskip例」と最新受入G5の「broken失敗／部分5／全滅1」が衝突。今回の明示受入を
  最新仕様として優先し、失敗test commit後、要件定義とbatch分類をfailedへ同期して回帰追加する。
- U-004修正: batchのAudioDecodeError item statusをfailedへ変更。正常＋decode failureはexit5、
  全decode failureはexit1、完了済み/dry-runだけskipとするようREADME/要件定義を同期し、
  fake normalizeがdecode failureを出すモデル不要回帰を追加。G5-01再試験待ち。
- G5-01修正後rerunは1.547秒、既存正常3 jobsをskipしbrokenをfailedとしてexit5で合格。
  G5モデル再利用の実ログ検証用にBackendPoolのASR/diarization load/reuseを非秘密INFOログへ追加し、
  CountingASRのload=1/reuse=1回帰へログ回数assertも追加。残りG5 case定義・実行へ進む。
- G5-02/04/05/07/08/09は合格。G5-05は再帰2件を60.906秒で処理、G5-09は実Ctrl+Cを
  14.968秒でexit 130として回収した。G5-03/06は受入検証器不具合H-002で失敗:
  既存jobのcreated_atを今回の処理順と誤認し、同一fingerprintを持つコピーが1 job IDを共有して
  manifest.input.pathが最後の入力へ更新される設計を考慮せずパスだけでjobを探索していた。
  failure commit後、G5-02の実CLI出力順を検査し、job探索をfingerprint由来IDへ修正してrerunする。
- H-002修正途中: fingerprint job探索後のG5-06は合格（ASR load=1/reuse=1）。G5-02/03は
  前後caseが同じjobの中断状態・export hash・衝突回避出力を変えるため、固定skip数と出力総数の
  完全一致では再試験不能だった。G5-02に固有export条件を与えて成功3/失敗1と実表示順を検証し、
  G5-03は衝突回避で増える出力を許容し完了件数以上を要求する。
- H-002最終: G5-02/03/06 rerun合格。G5全9 case（指示書11観点）がpass。
  WSLモデル不要testは87 passed/2 Windows-only skipped、ruff/format/mypy合格。
- Windowsモデル不要testは88 passed/1 failed。H-003: `test_remote_model_requires_token`が実機の
  導入済みcommunity-1を発見し、正当なlocal model経路でtoken不要になったため例外を発生させなかった。
  failure commit後、テスト専用model cacheを空directoryへ隔離してremote/token経路を保証する。
- H-003修正方針を精査し、空のmanaged cacheだけでは標準Hugging Face cacheを引き続き探索するため、
  `find_runtime_model=None`をmonkeypatchしてremote経路そのものを隔離する。実モデル保存先・.envは不変。
- H-003修正後: Windows targeted 1 passed、全モデル不要89 passed。WSLは87 passed/
  2 Windows-only skipped、ruff/format/mypyも合格。誤って部分同期されたWSL `.venv` は
  Linux uv 0.12.1 `uv sync --extra dev --link-mode=copy`で正常再構築済み。
- 受入G5完了: 最新rerunを含むG5-01〜09は全pass。指示書11観点（安定順、失敗後継続、
  集計、exit 5/1、recursive、include/exclude、dry-run、backend非再load、生成先除外、SIGINT 130）
  を実環境で確認。次はG6モデル管理。
- harnessのWindows peak memory=約5 MiBはconsole launcherだけの値で無効と判明（H-001）。
  G1結果を`68ace70`で先に記録後、Win32 Toolhelp snapshotで全子孫PIDを列挙し、同時点のworking
  set合計をpollして最大値を保持する方式へ修正。100 MiB確保の孫processで137,850,880 bytesを
  計測し、PID木のモデル不要回帰を含む83 tests、ruff/format/mypy合格。
- 利用者の管理済みpyannoteディレクトリを読み取り調査。Hugging Face tree metadata上は約32.1 MiB、
  実体は約1.1 MiBで、`embedding/pytorch_model.bin`（26,646,242 bytes）と
  `segmentation/pytorch_model.bin`（5,906,507 bytes）が欠落。完了metadataも存在しなかった。
- 原因はpyannote pipelineの導入判定が`config.yaml`の存在だけを見ており、verifyも同じ弱い判定を
  再利用していたこと。部分取得も候補として保持し、必須ファイル不足を明示する実装へ変更した。
- 再取得のWindows実機試験で、Hugging Faceのembedding一時ファイルパスがちょうど260文字となり
  `FileNotFoundError`になる第二原因を特定。snapshot downloadに`\\?\` extended-length pathを
  渡すと不足していた全10ファイルの取得が成功したため、managerへ恒久対策を追加した。
- 部分取得は一覧で「不完全」と表示し、downloadは既存保存先へ不足分を取得、removeは明示操作時に
  部分取得も削除できる。pyannoteの概算サイズを現行treeに合わせ100 MiBから34 MiBへ変更した。
- Windows実キャッシュで修正前の一覧=不完全1.1 MiB、verify=failed/exit 1を確認。extended-length
  pathで不足していた約31 MiBを再取得後、一覧=導入済み32.1 MiB、全モデルverify=exit 0、
  `PyannoteBackend.load(..., "cpu")` とunload=exit 0を確認。利用者の音声・動画は使用していない。
- 部分取得／再開とWindows path変換の回帰テストを追加。WSLモデル不要82 tests passed、
  ruff/format/mypy/lock/diff check合格。
- `input/.gitkeep`、`output/.gitkeep`を追加し、各フォルダ直下の任意内容をGit除外。既存の利用者
  入力1件は内容を読まず保持し、ignore対象であることだけ確認した。
- UTF-8 BOM付き`start.ps1`を実装。文字起こし、モデル、devices、jobs、config、setup profile、
  input/output Explorer起動へ番号メニューから到達できる。`.venv-windows`／Windows `.venv`の
  `utteran.exe`を優先し、未構築時はsetupを案内する。
- 文字起こしウィザードにinput一覧／一括／任意パス、output、実装済みASR backend、Whisper／
  Kotoba／任意登録ID・ローカルパス、device、言語、pyannoteモデル、話者数自動／固定／範囲、
  5形式、再帰・glob、resume／force、lock、config、ログ、dry-runを接続した。
- CLIに`--diarization-model`を追加。`--language auto`は設定上書きの`None`へ変換し、
  faster-whisperの言語自動判定を選べるようにした。引数変換の回帰テストを追加。
- Windows PowerShell 5.1でstart/setup Parser合格。startの終了、auto＋話者分離なし、Kotoba＋CPU＋
  話者3人＋5形式＋glob＋forceの2種dry-runがexit 0。models一覧、devices、jobs一覧、config pathの
  各メニュー接続もexit 0。
- WSL Python 3.11でモデル不要80 tests passed、ruff/format/mypy/lock/diff check合格。
  Windows Python 3.12でmodels/CLI重点22 tests passed。検査後はcpu profileへ復帰済み。
- 利用者環境にpyannote実モデルが導入されたことで、トークン不足テストがローカルモデルを解決し
  次段階へ進む環境依存を検出。テストではfind_runtime_modelを未取得へ固定して隔離した。
- 利用者実行で通常setupがモデルID入力待ちになる問題を受け、setupからモデル関連パラメータと
  対話取得処理を削除。環境構築・ffmpeg・秘密設定補助・devices診断だけを行い、完了後に
  `utteran models` を案内する責務へ変更した。
- モデルカタログ全5件へ表示名と用途説明を追加。Kotoba-Whisperを日本語向け、pyannoteを話者分離、
  OpenVINOをPhase 2推論未実装と明示し、80桁端末でも読める縦型一覧へ変更した。
- `utteran models download` のIDを任意引数化。対話端末で省略すると番号付き一覧を表示し、番号、
  カタログID、`<backend>:<model-id>`をカンマ区切りで複数選択できる。空入力は無変更終了、重複は
  除外、範囲外は設定エラー、非対話環境のID省略は取得せず明示コマンドを案内する。
- WSL Python 3.11でモデル不要79 tests passed、ruff/format/mypy/lock/PowerShell Parser合格。
  Windows Python 3.12でモデル／CLI重点21 tests passed。
- Windows実機の新setup cpu初回（CUDA profileからCPU版torchへ切替）と再実行がともにexit 0。
  モデルプロンプトなしでdevicesまで完走し、再実行は116 packages checkのみ。Windowsのモデル一覧
  5件表示も確認し、検査後はdev依存を除去してcpu profileへ復帰した。
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
- Windows setup初版を作成。cpu/cuda/intel profile、SkipModels/SkipFfmpeg/ModelDir/Models、
  uv sync、SHA-256 検証付き ffmpeg 配置、.env 非上書き、モデル事前取得、CUDA/全devices 診断を実装。
- setup初版はWindows PowerShell 5.1 Parser APIで `PowerShell syntax OK`。この時点では実際の
  sync、ネットワーク取得、ffmpeg展開、CUDA DLL解決はWindows実機未実行だったが、後続項目の
  とおり現在はcpu/cuda/intelの依存同期とデバイスprobeまで実機検証済み。
- Windows 実機で `setup.ps1 -Profile cpu` を2回実行。Python 3.12.0 と既存 ffmpeg は正常検出し、
  `.env` は非上書きだった。`uv` は未導入のため dependency sync、モデル取得、devices 診断を
  案内付きで安全にスキップし、2回とも同じ結果で終了した。永続 PATH と代表的な配置先も確認し、
  Python 3.12 実体は存在する一方 `uv.exe` は存在しないため、Python 誤検出ではないと切り分けた。
- uv導入後のWindows実機試験で、WSL用 `.venv/lib64` をWindows版uvが削除できずexit 2になる問題を
  再現。setupがLinuxレイアウトを検出した場合は既存 `.venv` を変更せず `.venv-windows` と
  `UV_PROJECT_ENVIRONMENT` を使うよう修正した。
- setupのprofileをCPU版PyTorch、CUDA 12.6版PyTorch、CPU版PyTorch＋OpenVINOへ分離。
  依存同期／devices／選択profile probeの失敗時は成功表示せずexit 1とし、依存する後続処理を省略。
- Windows APIでCPU 16 logical/8 physical、AVX2=true、AVX-512=falseを検出。仮想環境内の
  PyTorch/NVIDIA DLLディレクトリを登録し、CTranslate2からcuDNN/cuBLASを利用可能にした。
- CUDA 12.8版PyTorchはGTX 1070 Tiのsm_61非対応と実測したため、公式CUDA 12.6 indexへ変更。
  CUDA probeも単なるメモリ確保から、カーネル実行・CPU転送・同期を含む検証へ強化した。
- Windows Python 3.12.0 / uv 0.11.32でcpu/cuda/intelの初回と同profile再実行がすべてexit 0。
  2回目はpackage checkのみ。各profile + devでもモデル不要74 tests passed。
- cuda実測はPyTorch 2.11.0+cu126でGTX 1070 Ti CUDAカーネル成功、CTranslate2 4.8.1は
  cuda:0/int8、cuDNN/cuBLAS found、pyannote cuda:0を選択。intelはOpenVINO CPU/GPUを検出。
- `intel` extra にopenvino-genaiを追加し、後続のcpu/cuda/intel依存分離後の`uv.lock`は
  163 packagesで成功。
- README を全 Phase 2 CLI、resume/config hash、バッチ集計、Windows setup、Linux 手動導入、
  モデル／ジョブ保存先と削除、devices JSON、ライセンスへ同期。THIRD_PARTY_NOTICES も更新。
- job ごとの `utteran.log` handler を追加し、JSON Lines と最終 formatter の秘密マスクで段階遷移を
  記録。managed pyannote の token なし local 解決順序と、再帰バッチの job/output 自己入力を修正。
- quiet 時もジョブログには INFO の段階遷移を残し、コンソールだけを抑制するよう handler を分離。
  破損 manifest のジョブも `corrupt` として一覧・削除対象にできるよう復旧経路を補強した。
- 一時 Linux 静的 ffmpeg、合成 MP4、cached faster-whisper tiny、話者分離なしで Phase 2 実 E2E。
  初回は5段階、同条件の2回目は全段階 skip、形式変更は export-only、force は全段階再実行を確認。
- 最終検査: WSL Python 3.11.15とWindows Python 3.12.0でモデル不要74 tests passed。
  両OSのruff check／mypy strict（30 source files）、src・tests format check、
  `uv lock --check`（163 packages）がすべて成功。
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
  モデル未取得のため未実施。CUDAランタイム、実CUDAカーネル、CTranslate2 CUDA初期化は検証済み。
- `setup.ps1` のcpu/cuda/intel同期と診断はWindows実機検証済み。専用models CLIによる実モデル取得、
  ffmpeg未導入時のネットワーク取得、完全オフライン継続は未実施。

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
- 同じcheckoutのWSL用 `.venv` はWindowsと共有不能なため、setup時だけ `.venv-windows` を選び、
  `UV_PROJECT_ENVIRONMENT` を設定する。既存環境の削除や移動をせず冪等に共存させるため。
- cuda profileはPyTorch公式CUDA 12.6 indexを使用する。12.8 wheelがsm_61を非対応とする一方、
  12.6 wheelはGTX 1070 Ti上で実カーネルが成功し、CTranslate2用DLLも同梱することを実測したため。
- setupのprofile成功条件はコマンドexitだけでなく、cpuは両backend、cudaはCTranslate2とPyTorchの
  実CUDA probe、intelはOpenVINO初期化の成功とした。利用不能なのに成功表示しないため。
- 今回の利用者指示によりsetupは環境構築専用とし、モデル管理を `utteran models` へ分離する。
  通常のprofile切替がモデル選択プロンプトで中断せず、一覧・取得・削除の責務と導線を一か所に
  集約できるため。Phase 2初版のsetup事前取得要件をこの決定で更新した。
- モデル取得はID省略時に人間向け説明付き番号一覧を出し、番号を複数選択できるようにする。
  自動化と特殊用途モデルの直接指定を維持するため、従来の明示ID指定も残す。
- 明示IDは登録済みカタログIDに限定する。任意の未登録Hugging Faceリポジトリはbackend、形式、
  必須ファイル、runtime解決情報がなく、取得だけ成功しても利用不能になり得るため。日本語向け
  Kotoba-Whisperは登録済み `faster-whisper:kotoba-whisper-v2.0` として番号／ID双方で選択できる。
- start.ps1はPython側のpipelineや管理処理を再実装せず、対話結果を引数配列として既存CLIへ渡す。
  パスやglobに空白・記号があってもシェル再評価を起こさず、CLIとフロントの挙動差を抑えるため。
- start.ps1はUTF-8 BOM付きで追跡する。Windows PowerShell 5.1がBOMなしUTF-8の日本語をANSIとして
  誤読し、文字化けだけでなく構文エラーを起こすため。BOMを除去するformatterは使用しない。
- モデルの「導入済み」はディレクトリや設定ファイル1個の存在ではなく、形式別必須ファイルの
  完備で判定する。途中失敗を再開可能にするため部分ディレクトリは自動削除せず、状態と欠落名を
  表示して同じlocal_dirへのsnapshot downloadを再実行する。
- Windowsのモデル保存先そのものは通常表記を維持し、Hugging Faceへ渡すlocal_dirだけ絶対パスの
  extended-length表記へ変換する。これによりUIへ`\\?\`を漏らさず一時名のMAX_PATH超過を防ぐ。
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

- Windowsでffmpeg未導入時の取得・SHA-256検証、完全オフライン継続、専用models CLIによる
  gated／大容量実モデル取得を確認する。
- HF 利用条件へ同意済みのトークンとモデルが得られれば community-1、large-v3-turbo、
  それら実モデルを使うCUDA推論の未検証E2Eを実施する。

## 既知の落とし穴・回避方法

- バックエンド固有オブジェクトを pipeline/exporter に渡さず、共通 dataclass へ変換する。
- Hugging Face トークンは config.toml から無視し、ログと例外をマスクする。
- pyannote.audio 4.x の出力 API の差異をバックエンド内部で吸収する。
- WindowsとWSLで同じ `.venv` を共有しない。setupは `.venv-windows` を自動選択し、新しい
  PowerShellでは表示された `UV_PROJECT_ENVIRONMENT` を設定する。
- `start.ps1`の先頭UTF-8 BOMを保持する。PowerShell 7だけで検査せず、Windows PowerShell 5.1
  Parser APIと実行の双方を確認する。

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
- WSL Python 3.11.15: `uv run --no-sync pytest -m "not requires_model"` = 82 passed。
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
- Windows PowerShell 5.1 Parser API: `setup.ps1` と `start.ps1` は `PowerShell syntax OK`。
- Windows PowerShell 5.1: `start.ps1` の終了、2種類の文字起こしdry-run、models／devices／jobs／
  configのread-onlyメニュー接続はいずれもexit 0。
- Windows 実機 `setup.ps1 -Profile cpu` 2回: Python 3.12.0／既存 ffmpeg／既存 `.env` を正常検出。
  uv 未導入を明確に案内し、依存する処理を安全にスキップ。永続 PATH と代表配置先に `uv.exe` なし。
- モデル導線変更前のWindows Python 3.12.0 / uv 0.11.32:
  `setup.ps1 -Profile cpu|cuda|intel -SkipModels` は初回・同profile再実行ともexit 0。
  再実行はそれぞれ依存package checkのみ。
- モデル導線変更後のWindows `setup.ps1 -Profile cpu`: モデル入力なしで初回・冪等再実行・
  検査後復帰の3回すべてexit 0。`models list --available`で説明付き5件を表示。
- Windows Python 3.12.0: `tests/test_models.py tests/test_cli.py` = 22 passed。
- Windows各profile + dev: 全74モデル不要テストpassed。
- Windows cuda: PyTorch 2.11.0+cu126、GTX 1070 Ti sm_61でCUDAカーネル／同期成功、
  CTranslate2 cuda:0/int8、cuDNN/cuBLAS found、auto diarization=cuda:0。
- Windows intel: OpenVINO 2026.2.1、available devices=CPU/GPU。
- Windows環境の最終状態は利用者が最初に指定したcpu profileへ復帰済み。
- WSL/Windows双方でruff checkとmypy（30 source files）成功。mypyを両OSから同じ
  `.mypy_cache` へ同時実行するとinternal errorになったため、クロスOS検査は逐次実行する。
- `uv lock --check`: 成功、163 packages 解決済み。
- `git diff --check`: 問題なし。
- `要件定義.md` は Phase 1 設計書を基礎に、Phase 2 指示書の訂正5点と実効 output_dir の
  export hash 判断を同期済み。このため設計書原本との差分は意図した仕様更新。
