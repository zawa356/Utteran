# AI 作業状態

## Phase 4a 公開準備（2026-08-05、進行中）

- 指定4文書を読了し、`chore/phase4a-public-readiness`を作成した。履歴改変、force push、push、
  branch/tag削除は実施していない。
- Step 0で`origin`をfetchし、20 refs、111 commits、1,074 Git objects（到達可能1,052、
  到達不能blob 9/tree 13）、全111差分、全commit message/path/blobを走査した。秘密値候補0件。
  メール形式はcommit message 10件・過去差分7件、ユーザー絶対パス候補は`AISTATE.md`導入差分2件と
  同内容の到達不能blob 2件。値は報告・保存していない。現在の`AISTATE.md`では固有表記を一般化。
- GitHub側はIssues/PR/comments/Actions runsが0、release 1件はmetadata検出0・asset 0、Wikiなし、
  Discussions無効。詳細は`docs/公開履歴監査_Phase4a.md`。履歴維持／書換え／非公開化の影響を整理し、
  利用者判断までは履歴を維持する。
- 再実行可能な`tools/public_history_scan.py`、値を含まないJSON報告、現在tree用blocking gate、
  `.gitignore`実効性テストを追加した。gitleaksは環境になかったため自前scanを使用。
- Step 1としてGitHub Actionsを追加。Linuxでruff/format/mypy/lock/モデル不要test/BOM byte/
  public scan、Windowsでモデル不要test/全PS1 Parser/Windows PowerShell 5.1実起動を行う。
  CIはnative build、実model、GPU、長時間、性能を対象外とし、実質的なrelease品質保証は
  `tools/acceptance`が担うことを要件定義23章とハーネスREADMEへ明記した。
- Linux CI相当をWSL Python 3.12.3で実行し、当初はWindows専用subprocess定数のmypy参照1件と、
  profile directory/受入実行ファイルoverrideをWindows固定したテスト3件が失敗した。定数を
  `getattr`でOS条件解決し、テストを`venv_dir_name()`利用、ハーネスoverrideをOS共通へ修正。
  再実行はruff/format/mypy/lock/BOM/public gate合格、モデル不要201合格・Windows限定2 skip。
  Windowsはモデル不要203合格、全PS1のParser/PowerShell 5.1実起動、全品質gate合格。
- Step 2: benchmarkの既定測定長を短いfixtureでなく指定WAV全体（`full`）とし、
  `--durations 180,900,full`でprefix一時WAVによる複数長測定を追加。15分を測定時間と代表性の
  妥協点に定め、未満ではPhase 3dの180秒/24分46秒順位逆転を画面・JSONへ警告する。
  JSONはschema v2、`--apply`は最長測定の最速variantと`benchmark_duration_seconds`を保存し、
  測定metadataはASR config hashへ含めない。
- autoのIR状態判定は今回見送った。選択時点で設定modelからGGML実体と量子化別IR aliasの完全性まで
  検証し、fallbackとの整合を変える必要があり、既存autoの安全性に対して変更範囲が過大なため。
  Phase 4b以降でmodel-aware autoとして追加実測と合わせて検討する。
- Step 3: READMEを公開向けの短い導入・quick start・主要操作へ再構成し、英語short版を追加。
  詳細仕様／受入／監査／release手順へlinkした。THIRD_PARTY_NOTICESへPhase 3のwhisper.cpp、GGML、
  Silero VAD、OpenVINO、Vulkan/shadercとruntime依存を補完し、全modelの最新条件確認・必要な同意を明記。
- GitHubには2026-08-02公開の`v0.0.1`releaseが実在したが、tag時点の変更履歴が未releaseのままだった。
  過去を改変せず初期snapshotと位置づけ、次の正式pre-1.0候補を`v0.1.0`とした。pyprojectと
  `__version__`を0.1.0へ統一し、SemVer、changelog切出し、acceptance/license/security gate、
  tagを移動しないrelease手順を`docs/リリース手順.md`と要件定義24章へ追加。
- 任意整備は、SECURITY.mdはprivate reporting/contactが未設定のため安全な非公開窓口を示せず見送り、
  CONTRIBUTING/Issue/PR templateは外部contribution workflow未確定のため見送り。repository説明/topic/
  badgeはGitHub設定変更や未push CIへの依存があるため実施しない。公開後の次段階でownerが判断する。
- Phase 4a最終再走査はcommit `a36b917`までの20 refs/115 commits/1,144 objectsを完了。
  現在treeはblocking 0、公開refの秘密値候補0。ローカル到達不能blobにscanner回帰testが
  意図的に使った秘密値形式1件があるが、値を表示せずtest contextから非credentialと判定。
  objectの削除/GC、履歴書き換え、pushは行っていない。G11公開文書受入は2/2 pass。
- Phase 4a追加照合は`input/`の内容を開かずfile名とWindows環境から36のhash patternを
  Git対象外の`output/`へ生成。21 refs/116 commits/1,152 objects/116差分/現在119 filesを
  照合した。組織domain、email local部、組織名、入力file名、Windows user名は全0件。
  初回の短い日本語／file片抽出は一般語を過剰検出したため、最長日本語列と十分長い
  要素へ保守的に限定して再走査した。値とdigestはGit/報告/標準出力に未記録。
- 照合0でも過去のuser絶対pathを除去する価値があり、追加指示で承認済みのため、
  完全backupと復元試験後に全branch/tagの履歴書き換えを実施する。pushは行わない。

## Phase 3d 是正（2026-08-04〜2026-08-05、完了）

- 固定checkoutの`examples/cli/cli.cpp`で`--max-context`、Sileroモデル必須の`--vad`、
  entropy/logprob/no-speech閾値、温度fallbackの正確な名前と既定を確認した。
- 24分46秒実会議WAV、Vulkan、large-v3-turbo-q5_0、反復保険無効で切り分けた。B1
  （flash有効/DTWなし）は103.293秒・324 segments・5559文字・最大反復3、B2
  （flash無効/DTWなし）は144.523秒・297・5606・最大2、B3（flash無効/DTWあり）は
  143.477秒・297・5606・最大2。B2/B3の統計一致から出力差はDTWでなくflash無効化が要因。
- C0対策なしは289.935秒・571 segments・7186文字・最大150連続・反復追加343。C1前文脈遮断のみ
  110.497秒・313・5521・最大3・追加3、C2 VADのみ103.582秒・191・5659・最大2・追加2、
  C3 entropy 2.4のみ193.247秒・480・6284・最大31・追加90、C4 logprob -1.0のみ301.209秒、
  C5 no-speech 0.6のみ292.881秒で後二者はC0と出力統計一致。全既定組合せC6は86.473秒・
  280・5425・最大2・追加1。日本語文字比0.969以上、空segment比0。閾値は各1点測定。
- 実測から`no_context=true`とdecoder既定を維持、VADは有効だがsegment構成変化と追加modelのため
  opt-in、反復保険は4から10へ緩和。Silero v6.2.0をカタログ/download/verifyへ統合した。VAD実測で
  非ASCII model pathのnative crashと不正UTF-8 JSONを検出し、ASCII stagingと置換warningで修正。
- Intel profile（Python 3.12.13）、Arc 140T、180秒WAV、large-v3-turbo-q5_0、単語TSなしで
  warmup 1 + 3回測定。中央値はVulkan 20.488秒（8.787x）、ovvk 32.564秒（5.528x）。
- 音声連結実測: Vulkan ASR 25/50/100分は102.946/220.690/443.087秒、peak RAM
  1.470/1.738/2.277 GB。XPU話者分離は212.523/401.551/811.918秒、5.407/5.584/6.097 GB。
  CPU話者分離25/50分は655.909/1128.586秒、2.753/2.907 GB。150分は時間制約で省略し、
  100分までからXPU 120分約6.3 GB、150分約6.6 GBと外挿。68 GB RAM本機で2時間級OOM見込みなし。
- R-4の中間時点では既存G系118ケースとP14 3ケースだけが単一`cases.json`へ統合済みで、
  P0〜P13/P15/P16の分類と統合全件実行が残っていた。この残件は後続の「Phase 3d 最終指示」で
  移植・手動手順・他試験への委譲を明記し、全件実行まで完了した。

## Phase 3d 最終指示（R-7・R-4完遂、2026-08-05）

- **R-7 再測定**: whisper-cliへの引数がR-2時点の測定から不変であることをコード差分で確認した
  うえで、`utteran benchmark`により全構成・単語TSあり/なし・VAD有効・実音声長を再測定した。
  180秒fixture・warmup1・3回中央値（TSなし/TSあり）: cpu 148.460s/183.135s、
  openvino 30.587s/34.138s、vulkan 15.363s/22.310s、openvino_vulkan 19.638s/24.086s、
  faster-whisper 79.551s/74.672s。VAD有効時のvulkan（TSなし）は14.322sでVAD無効時と有意差なし。
  いずれもR-2時点よりやや高速（cpu/openvino/vulkanで約10〜13%）だが引数不変のため測定ばらつきと
  判断し、対策自体による高速化ではない。
- **重大な発見**: 180秒fixtureではvulkan(15.363s)がovvk(19.638s)より高速という順序が維持される一方、
  24分46秒の実会議WAV（単語TSなし、1点測定）ではvulkan 110.730s・ovvk 65.141sと**順序が逆転**した
  （ovvkが約41%高速）。180秒の比から単純外挿するとvulkan約127s・ovvk約162sの見込みだが、
  実測はovvkが外挿より大幅に高速で、OpenVINOエンコーダの初期化コストが長い音声で償却される
  ためと推測されるが単一環境・単一素材の1点測定であり断定していない。
- **auto順序の決定**: `vulkan`優先の既定順序は変更しない。理由は(1) IR未変換環境でも`auto`が
  確実に動作すること（`_choose_variant`はnative build有無のみ判定し、モデル別IR生成状態までは
  見ないため、既定でovvkを選ぶとIR未生成環境で初期化失敗を誘発しうる。安全にするには追加実装が
  要り本ステップの範囲を超える）、(2) 短い素材でも長い素材でも追加作業なしに確実に動く構成を
  既定にする方針（R-2の判断）を継続するため。ただし主用途（30分〜3時間の会議）ではovvkが
  大きく有利な場合があるため、README/要件定義に長時間音声での逆転と`--variant openvino_vulkan`
  明示指定の推奨を明記した。`devices`のauto表示・理由文言は順序を変えていないため追従不要。
- README・要件定義・受入試験報告_Phase3.mdの対策前の性能値へ「対策前の測定値であり現在は
  改善している」旨の注記を追加した。
- **R-4完遂**: `tools/acceptance/harness.py`のCaseへ`requires`（profile/backends/native_variants/
  models/cuda/xpu）・`destructive`・`estimated_seconds`を追加し、`devices --json`/
  `models list --json`の1回限りスナップショットと突き合わせて実行可否を判定する
  `unmet_requirements`/`fetch_environment`を実装した。`destructive`ケースは既定除外し
  `--include-destructive`または明示`--group`でのみ実行する。`run_selected()`/`RunSummary`で
  Python APIと機械可読サマリーJSON出力を追加した。既存G13（CUDA耐久）へ`requires: {cuda: true}`
  を付与し、本機（NVIDIA不在）では理由付きskipとして扱われるようにした。
- P0〜P11から74ケースを新規追加した（P12〜P16は自動化・他手段での充足・手動手順書のいずれかに
  分類し`docs/受入試験_手動確認手順書.md`と`tools/acceptance/README.md`に記録、詳細は次項）。
  ほぼ全ケースが既存の`command-output`/`json-output`/`validate.py formats|json|equivalent`を
  再利用でき、新規に追加したのは`scenarios.py`の`native-manifest`（manifestのcommit・
  非可搬パス不在確認）・`profile-isolation`（プロファイル更新が他プロファイルのtorchへ
  影響しないことの確認）と、`validate.py`の`words`（単語タイムスタンプ有無確認）の3つのみ。
- **製品バグを発見・修正**: 本機（cp932ロケール）で`sys.stdout`がコンソール非接続時
  （パイプ・リダイレクト・subprocess capture）に既定でcp932になり、`devices --json`の
  `auto_selection.notes`等に含まれる日本語がpipe経由で破損する（無効なJSONになる場合すらある）
  不具合を発見した。R-4のrequires判定が依存する`devices --json`のパイプ読み取りで顕在化した。
  `cli.py`の`main()`callbackで`sys.stdout`/`sys.stderr`を`UTF-8`へ`reconfigure`して修正した。
- **製品バグを発見・修正（重大）**: 新規P1-1ケース（`setup.ps1 -List`をWindows PowerShell 5.1
  すなわち`powershell.exe`から直接実行）で`setup.ps1`が構文エラー（`Unexpected token`）で
  即座に失敗することを発見した。同様に`run.ps1`も日本語文字列が文字化けしたうえで構文エラーに
  なることを確認した。原因は両ファイルにUTF-8 BOMがないこと。`start.ps1`は既に同じ理由で
  BOM付き追跡と明記されていたが（既知の落とし穴参照）、`setup.ps1`/`run.ps1`は対象外のままだった。
  過去のWindows PowerShell 5.1 Parser API検査（静的構文検証）はこの実行時破損を検出できず、
  合格し続けていた。両ファイルへUTF-8 BOMを追加し、`powershell.exe -File`での実行で
  再現しないことを確認した。**この2ファイルはWindows PowerShell 5.1環境（README記載の
  対応環境）で今回の修正前は起動不能だった可能性が高い。**
- **統合ハーネス最終実行**: 2026-08-05 11:37:54+09:00にG/P統合192ケースの実行を開始し、
  初回全件は57分16秒で164合格・20失敗・8スキップだった。P系77件は77件すべて合格し、
  8スキップはNVIDIA不在のG13だった。旧G系20失敗をPhase 2固定前提、CUDA要件メタ情報不足、
  蓄積ログ集計、結果パス固定、Windows PowerShell 5.1互換性へ分類して修正・再実行した。
  2026-08-05 13:03:31+09:00時点のID別最新結果は**177合格・15スキップ・0失敗**。
  15スキップはG13 8件とG14 CUDA検証7件で、すべて`CUDA hardware not present`の理由付き。
  P1のCPU/Intel/Vulkan更新、Vulkan削除→復元、native clean→build、モデル削除→再取得、
  OpenVINO IR生成→削除、P14長時間（話者分離なし102.7秒、XPUあり378.5秒、resume 1.1秒）も合格。
- **最終実行で追加修正**: `setup.ps1`のPowerShell 5.1パイプUTF-8復号と複数行Python probe、
  `start.ps1`のPowerShell 5.1非対応な3引数`Join-Path`、シナリオの非対話stdin/UTF-8読取り、
  同一job IDへ集約されるbatchの直近ログ窓、任意`results.jsonl`を参照する`{results}`を是正した。
  全192ケースへ`requires`/`destructive`/`estimated_seconds`を明示した。統合結果の詳細は
  `docs/受入試験統合結果_Phase3d.md`を参照。**Phase 3dは完了。**

## 現在のフェーズと進捗

- Phase 3受入試験（2026-08-04、`test/acceptance-phase3`）: P0合格。P1で`setup.ps1`が
  Python CLIの日本語JSON／非ASCIIユーザーパスを誤デコードし、profile検証と既定profile設定が
  失敗する問題を検出。子processをUTF-8固定し環境変数を復元する修正を行い、CPU/Intel/Vulkanの
  setup、既定変更、Vulkan削除・復元、torch profile非干渉を実機で再確認した。CUDAは作成していない。
  P3ではmanifestにprofile固有OpenVINO絶対パスが残る問題と、部分native buildが未指定構成を
  manifestから消す問題を検出・修正。4構成復元、CPU force build、CPU clean→build後も全4構成が
  runnableで、manifestはv1.9.1 commit一致・profile固有OpenVINOパスなしを確認した。
  P6で未ビルドwhisper.cpp構成のエラーに`native build --variant`案内がない問題を検出・修正。
  P14（本機の実ファイルは仕様想定2時間20分でなく24分46秒）でwhisper.cppのゼロ長segment/wordを
  検出。共通型変換で除外してsegment fallbackする修正と回帰試験を追加し、長時間再試験対象とした。
  再試験でゼロ長は0件になったが、TSなしASRに同一segment最大72連続を検出。5回目以降の完全一致
  segmentを抑制する回帰を追加し、P14話者分離なしだけ再試験対象とした。
- 運用改善（2026-08-04）: pipelineの各実行ステージを`perf_counter`で計測し、正常完了時に
  audio/asr/diarization/merge/exportと実行フェーズ合計を`HH:MM:SS.mmm`で最終表示するよう変更。
  resume再利用ステージは除外し、フォルダ一括は成功ジョブ分をステージ別に合算する。
- Phase 3c follow-up（2026-08-04）: Windows対話フロントがASR用`openvino_vulkan`を共通の
  `--device`でpyannoteにも渡すため、話者分離モデル読み込み時に失敗する不具合を実会議ジョブで
  特定。モデル単体のXPUロードと同ジョブの`auto`完走履歴からモデル破損ではないことを確認した。
  `--asr-device`／`--diarization-device`を追加してメニュー選択を分離し、従来の`--device`は互換維持。
  pyannoteの非対応デバイス例外を一般エラーへ潰さず、そのまま案内するよう修正した。実データの
  内容および`.env`の値は参照・記録していない。同じジョブをASR=`openvino_vulkan`、話者分離=
  `auto`でresumeし、audio/asr再利用、diarization/merge/export実行が230.4秒・exit 0で完走した。
- Phase 3c Step 0（2026-08-03、分岐A）: 有効な`.env`トークンを値非参照のまま使用して
  community-1完全モデルを取得。Arc 140Tで`Pipeline.to(xpu:0)`と30秒実音声の実pipelineが
  環境変数fallbackなしで完走した。CPU/XPUはいずれも1話者・5区間で境界まで完全一致し、
  XPU同一条件2回も完全一致。推論秒はCPU 12.438、XPU初回10.676、同一process再実行3.300。
  pyannote XPU対応を実装する分岐Aに確定した。実データの内容は記録していない。
- Phase 3c Step 1/2着手: PyTorch XPU実kernel probe、CUDA→XPU→CPUの話者分離auto選択、
  `xpu`/`xpu:N`解析、intel profile不整合案内、auto時だけのload失敗CPU退避、共有RAMを説明する
  XPU OOM変換、`unload()`のXPU cache解放、devices JSON/表示を実装。対象14テスト、ruff、mypy合格。

- Phase 3b（whisper.cpp ASR）: `feature/phase3b-whisper-cpp`でStep 1〜7を実装・Intel実機検証済み。
  当時保留したpyannote gated実モデルとの結合比較はPhase 3受入試験P9/P10/P14で解消した。
- Phase 3b Step 2調査: 2026-08-03にHugging Face APIの`ggerganov/whisper.cpp` siblingsを
  取得し、実在するGGML 33ファイルとbyteサイズを確認して登録した。v1.9.1
  (`f049fff...`)の`cli.cpp`はOpenVINO deviceの`--ov-e-device`だけを公開し、IR pathには
  `nullptr`を渡す。`whisper.cpp:3350-3578`でGGML名から`-encoder-openvino.xml`を導出するため、
  量子化違いには規約名リンク（不可ならコピー）が必要と判断した。
- Phase 3b Step 3調査: upstream v1.9.1変換scriptと参考実装をdiffし、upstreamには
  `from openvino.runtime import serialize`（現行OpenVINOでは不可）と、Windowsでexporterが
  file handleを遅延解放した際に`shutil.rmtree`が変換後に失敗する問題が残存すると確認した。
  `from openvino import serialize`と`ignore_errors=True`の2修正を持つscriptをvendor同梱した。
  モデル不要のIR命名・量子化alias試験は合格。実IR変換はStep 7で大容量重みを用いて検証する。
- Phase 3b Step 4: whisper.cpp backend本体を実装。v1.9.1 `cli.cpp`で確認した`-m/-f/-l/-bs/
  -ojf/-of/-pp/-t/--prompt/-oved/--device/--no-gpu/--dtw/--no-flash-attn`だけを使用する。
  stderr進捗、Windows process tree停止、秘密値mask、動的OpenVINO DLL、temporary JSON、共通型変換、
  DTW全`-1`時の単語破棄を実装。実モデルE2EはStep 7で実施する。
- Phase 3b Step 5: devices auto選択をCUDA→ovvk→vk→ov→faster-whisper CPUへ拡張。
  v1.9.1/OpenVINO/Vulkanの限定した初期化失敗patternだけを対象に、auto時に次のGPU構成へ
  1回だけ退避する。明示variantは退避しない。注入probe/model不要試験でovvk選択を確認。
- Phase 3b Step 6: `models list --json`を追加。start.ps1はactive profileを表示し、devices JSONの
  backend/auto/native/CUDA可用性とmodels JSONの導入済み状態から選択肢を動的生成する。
  空の場合はnative buildまたはmodels downloadを案内する。PowerShell Parser検査合格。
- Phase 3b Step 7実機（ZL-PC0010 / Core Ultra 7 255H / Arc 140T / Windows 11）:
  - 既定native保存先のPhase 3a成果物が現存しなかったため再buildし、cpu/openvino/vulkan/
    openvino_vulkan全4構成成功（493.4秒）。v1.9.1 commit一致、全exe runnable。
  - large-v3-turbo-q5_0（実ファイル574,041,195 bytes）を取得。OpenVINO IR変換中、PyTorch
    exporterがUnicode記号をcp932へ出せず失敗する新規問題を発見し、subprocessをUTF-8固定して
    修正。再試行52.9秒でIR生成、q5_0 aliasを`models verify`で正常確認。
  - 日本語ユーザー名を含む既定model pathで4構成とも0xC0000409終了する新規問題を発見。
    GGMLと隣接IRを推論中だけASCII-safe temporaryへhardlink（不可時copy）して修正。CPU再試験と
    残り3構成がすべて完走。
  - 10.745秒TTS合成日本語で4構成とも3 segments/46 words、segment外単語時刻0件。
    raw full JSONは通常token 42件に実`t_dtw`を持ち、範囲28〜984、最終segment end 9860ms。
    v1.9.1ソースの`t_dtw/100`表示と実範囲が一致し、**t_dtwは10ms tick**と確定。
    faster-whisperは同音声39 wordsでwhisper.cppは約18%多いだけのためalignment閾値は維持。
  - 180秒合成fixtureのend-to-end秒（TSあり/なし）: cpu 463.352/391.030、openvino
    62.719/57.674、vulkan 40.863/30.699、ovvk 33.585/26.421。全exit 0。faster-whisper CPU
    （既存どおりTSあり）は178.344秒。ovvkのTSなしはありより約21%高速。
  - 実機`devices`はautoをwhisper-cpp/openvino_vulkanと理由付き選択し、auto E2Eも成功。
  - 当時未実施だったcommunity-1実モデルとの結合比較は、後続のPhase 3cとPhase 3受入試験で解消した。

- Phase 1（骨格と最小動作）: 実装完了。受入試験でfaster-whisperとgated pyannoteの
  CPU/CUDA実モデルE2E、5形式出力を検証済み。
- Phase 1 初回コミット: `83a4b29 feat: implement Phase 1 transcription pipeline`。
- Phase 2 実装コミット: `54fa46d feat: implement Phase 2 operational workflows`。
- Phase 2（実運用機能）: 実装・受入試験完了。ジョブ／レジューム、フォルダバッチ、devices、
  モデル／ジョブ／設定管理CLI、Windows setupを実装。Windows実機でcpu/cuda/intel profileと、
  large-v3-turbo＋community-1のCPU/CUDA実処理を検証済み。
- Phase 2 follow-up（モデル導線改善）: 実装・Windows/WSL検証完了。setupからモデル管理を分離し、
  models CLIへ説明付き一覧、番号選択、従来の明示ID指定を集約した。
- Phase 2 follow-up（Windows対話フロント）: 実装・Windows/WSL検証完了。input/output運用と、
  start.ps1からtranscribe・models・devices・jobs・config・setupへ到達する番号メニューを実装した。
- Phase 2 follow-up（不完全モデル検出）: 実装・Windows/WSL検証完了。利用者環境でpyannote
  community-1の重みが欠落した約1.1 MiBの部分取得を「導入済み」「正常」と誤判定する問題と、
  Windows MAX_PATHによる取得失敗を修正した。
- Phase 1/2受入試験: G0〜G14完了。最新115/115ケースpass。開始コミット`57ecbb3`から
  専用branch`test/acceptance-phase2`を作成し、実施結果を`docs/受入試験報告.md`へ集約した。
  試験終了コミットは`982c5f5`。最終profileはCUDAを維持し、2時間job
  `7be37b2d3fc10277`と約56 MiBの再現fixtureを保持する。
- Phase 2 follow-up（kotoba-whisper-v2.0クラッシュ修正）: 実装・Windows実機検証完了。
  受入試験ではkotoba-whisper-v2.0の実推論E2Eを検証していなかったため、受入完了後の
  利用者実運用（start.ps1文字起こしウィザード、実会議MP4、CUDA）で初めて発現した
  ネイティブクラッシュ（U-005）を修正した。
- `docs/utteran_Phase2_指示書.md` 全399行、既存状態、要件定義、変更履歴を読了し、
  コード着手前の指定仕様訂正5点を要件定義へ反映済み。
- `docs/utteran_設計書.md` 全715行を読了。
- コード着手前の必須4文書を作成。
- Phase 3a（実行環境分離・whisper.cppネイティブビルド機構・Phase 3b事前調査）: 実装完了。
  作業branch `feature/phase3a-environments`。Step 0〜6すべて完了し、実機で個別・end-to-end
  双方を検証済み（詳細は「直近の作業内容と結果」）。受入試験（G0〜G14相当の専用試験）は
  本セッションでは未実施。次セッションでの受入試験実施が前提。

## 事前調査結果（Phase 3a Step 0）

**指示書の前提と異なる点（最重要）**: 本調査は受入試験時と別のWindows実機
（`ZL-PC0010`、Intel Core Ultra 7 255H、**Intel Arc 140T iGPU（32 GiB共有VRAM表示）**、
NVIDIA GPUなし、RAM 68,161,626,112 bytes、Windows 11 Business 10.0.26200、Cドライブ空き
約168 GiB）で実施した。既存の`.venv`・`.venv-windows`は本機に存在しない（削除禁止の対象が
そもそも無い）。uvは本機未導入だったため、公式スタンドアロンインストーラーで
`%USERPROFILE%\.local\bin`へ導入した（利用者にuv導入方針を確認し承認を得た。uv 0.12.1、
PATH永続化確認済み）。Visual Studio Community 2022（17.14.37411.7）とVS同梱のCMake
3.31.6-msvc6／Ninjaが利用可能。Vulkan SDK 1.4.350.0が導入済みで`glslc`／`vulkaninfo`とも
利用可能。この実機の性質上、cuda profileはvenv作成のみ可能でprobeは必ず失敗するため、
利用者確認のうえ**作成せず「未検証」として扱う**。

- **I-1（whisper.cppバージョン選定）**: 最新安定タグは`v1.9.1`
  （commit `f049fff95a089aa9969deb009cdd4892b3e74916`、2026-06-19）。参考実装の`v1.8.6`
  （`23ee03506a91ac3d3f0071b40e66a430eebdfa1d`）より新しい。利用者確認のうえ`v1.9.1`を採用。
  実際に`--branch v1.9.1 --depth 1`でclone、CPU/Vulkan双方の`whisper-cli`ビルドに成功し、
  実推論も正常動作したため後退の必要なし。**採用: v1.9.1**。
- **I-2（単語レベルタイムスタンプ）**: 実機で検証済み。
  - `--dtw`プリセット一覧はソース確認（`examples/cli/cli.cpp`）で
    `tiny(.en)/base(.en)/small(.en)/medium(.en)/large.v1/v2/v3/v3.turbo`の12種。
    **`large-v3-turbo`用プリセット`large.v3.turbo`は存在する**
    （`WHISPER_AHEADS_LARGE_V3_TURBO`、layer 2〜3の6 head、`src/whisper.cpp:395,409`）。
    turboのdecoder層数（圧縮後で浅い）に対し参照層番号が小さく収まっており、
    ソースを読む限りU-005（CTranslate2/kotoba-whisper-v2.0のalignment_heads不整合による
    ネイティブクラッシュ）と同種の層番号超過は見当たらない。ただし実際のlarge-v3-turbo
    ggmlモデルでの実行確認はPhase 3aの範囲外（時間・帯域の都合、確認は今後）。
  - **重大な非自明挙動**: `whisper-cli`は`flash_attn`が既定で`true`
    （`examples/cli/cli.cpp:79`）であり、**`--dtw <preset>`を指定してもflash attention有効時は
    警告ログ`dtw_token_timestamps is not supported with flash_attn - disabling`を出して
    無言でDTWを無効化する**（エラーにならない）。`--dtw base --no-gpu`のみでは
    `t_dtw`が全トークンで`-1`のままだったが、`--no-flash-attn`（`-nfa`）を追加すると
    `alignment heads masks size = 256 B`のログが出て`t_dtw`に実値が入ることを実機確認した。
    **Phase 3bでは単語タイムスタンプ利用時に`--no-flash-attn`を必須で付与すること。**
    指示書はこの依存関係に触れていない。
  - `-ojf`（`--output-json-full`）のJSON構造を合成日本語音声（後述）で実測。
    トップレベルキー: `systeminfo, model, params, result, transcription`。
    各segmentは`timestamps{from,to}(文字列 "HH:MM:SS,mmm"), offsets{from,to}(ms整数),
    text, tokens[]`を持つ。各tokenは`text, timestamps, offsets, id, p, t_dtw`を持つ
    （`t_dtw`は非text特殊token（`[_BEG_]`等）では常に`-1`、DTW無効時は全token`-1`）。
  - 日本語のトークン粒度: base多言語モデルのBPEトークナイザーは、かな・漢字1文字未満の
    UTF-8バイト断片単位でトークン化される場合が多く（1トークン=1文字ではない）、
    `whisper-cli`自身はデフォルトでトークンを単語へ統合しない。単語単位化には
    `--split-on-word`（`-sow`）と`--max-len N>0`の併用が必要（`cli.cpp`確認）。
    `align.py`の単語数ベース閾値（`min_segment_words`等）にPhase 3bで影響するため、
    Phase 3bでは生トークンではなく`-sow`併用時の単語単位出力を基礎にする設計が必要。
  - 使用モデルはOpenAI公式`large-v3-turbo`ではなく`ggml-base.bin`
    （帯域・時間の都合、構造検証目的で代用）。JSON構造・DTW有効化条件はモデル非依存の
    はずだが、large-v3-turbo実機での最終確認はPhase 3bで行うこと。
- **I-3（Vulkanビルド前提条件）**: 実機で完全ビルド・実推論に成功。
  - `-DGGML_VULKAN=ON`のCMake configureで`Vulkan found`と共に
    `glslc`／`glslangValidator`のcomponentが要求される
    （`find_package(Vulkan ... COMPONENTS glslc glslangValidator)`相当、
    ログ: `Found Vulkan: ...vulkan-1.lib (found version "1.4.350") found components: glslc glslangValidator`）。
    **`glslc`はビルド時に必須**。入手経路はVulkan SDK（本機は`C:\VulkanSDK\1.4.350.0`、
    環境変数`VULKAN_SDK`設定済み）。SDK同梱の`glslc`のみで確認、他経路は未調査。
  - 指示書の懸念どおり、**`vulkaninfo`の存在だけではビルド前提を確認できない**
    （`vulkaninfo`はランタイム確認、ビルドには別途SDKのCMake/glslcコンポーネントが必要）。
    実際には本機は両方導入済みのため、ビルド前提の欠如を単独では再現できなかった。
  - ビルドは`Ninja`ジェネレータ、VS2022同梱MSVC 19.44で実施。359ターゲット中、
    shader-gen関連が大半（359ステップ）。ビルド成果物サイズ: `cpu`構成35 MiB、
    `vulkan`構成559 MiB（大量の`.comp`シェーダーオブジェクト）。
  - 実推論確認: `whisper-cli --device 0`でVulkan0（Intel Arc 140T）を検出・使用し、
    exit 0で完走（load 282ms、encode 1494ms、total 4056ms、30語程度の短い日本語音声）。
- **I-4（uvのconflicting extras / explicit index）**: 独立検証プロジェクトで実機確認。
  - `[tool.uv] conflicts`と`[[tool.uv.index]] explicit = true`、
    `[tool.uv.sources]`のextra別ルーティングはuv 0.12.1で仕様どおり動作する。
    `cpu`/`cuda`/`xpu`を`conflicts`に登録した状態で単一`uv.lock`が生成でき
    （102 packages resolved）、`uv lock --check`も合格。
  - **同時指定は正しく拒否される**: `uv sync --extra cpu --extra cuda`は
    `error: Extras \`cpu\` and \`cuda\` are incompatible with the declared conflicts`で
    exit 2。
  - プロファイル別venv分離も実機確認: `UV_PROJECT_ENVIRONMENT`を切替えて`--extra cpu`のみを
    別venvへsyncすると、他venv（xpu等）に影響せずtorch cpu版だけが入ることを確認。
  - **重大な非自明な制約（未文書化のuv要件）**: torch xpu版は`triton-xpu`パッケージに
    transitiveに依存するが、`torch`/`torchaudio`同様に`tool.uv.sources`で`pytorch-xpu`
    explicit indexへルーティングしても、**`triton-xpu`が`xpu`extraの
    `project.optional-dependencies`に直接列挙されていない限りuvはエラーで拒否する**
    （`Source entry for 'triton-xpu' only applies to extra 'xpu', but 'triton-xpu' was not
    found under the project.optional-dependencies section for that extra`）。
    実装では`xpu = [..., "triton-xpu; sys_platform == 'win32'"]`のように明示追加し、
    対応する`tool.uv.sources`エントリも追加する必要がある（Linux版wheelも存在するが
    Windows専用切り分けの要否は未検証のため`win32`条件を付与）。指示書のI-4/Step 1の
    実装例にはこの追加が欠落しており、そのままでは`uv lock`が失敗する。
  - `pytorch-triton-xpu`（旧来torch xpu版が依存していた別名パッケージ）はPyPI上に存在せず、
    Windows向けwheelも確認できなかった。torch>=2.11.0+xpuでは`triton-xpu`（ハイフン区切り、
    別パッケージ名）が正しい依存名である。
- **I-5（torch XPUとopenai-whisperの依存衝突）**: 実機確認、衝突なし。
  `xpu + openvino + whisper-cpp`相当のextra組み合わせ（`torch==2.11.0+xpu`、
  `openai-whisper`、`onnxscript`等54 packages）を同一venvへsyncし、`torch`が
  CPU版へ上書きされないことをsync後のパッケージ一覧で確認した（`torch==2.11.0+xpu`のまま）。
- **I-6（pyannote 4.0.7のXPU動作可否）**: 部分確認（Phase 3cの課題として残置）。
  - `torch.xpu.is_available()`は`True`、`torch.xpu.get_device_name(0)`は
    実機の`Intel(R) Arc(TM) 140T GPU (32GB)`を返す。基本ランタイム初期化は正常。
  - pyannoteのsegmentationモデルが使う代表的な層（`Conv1d`, 双方向`LSTM`,
    `InstanceNorm1d`）をXPUデバイス上で直接実行し、forward計算・CPUへの転送まで
    エラーなく成功した（未対応オペレーターは検出されなかった）。
  - **community-1実パイプラインでの完全E2Eは未確認**。本機の`.env`にはgatedモデルの
    有効なHFトークンが設定されておらず（`.env`は内容を読まず、取得の成否だけで判定。
    プロジェクト規約に従い値は不参照）、`Pipeline.from_pretrained(...)`が
    `GatedRepoError`で失敗した。基本演算は動作することが分かった一方、実モデルの
    重み・グラフ構造まで含めた完全な動作保証はできない。**Phase 3cで有効なトークン環境
    にて再検証が必要**。
- **I-7（ディスク使用量の実測）**: 部分実測。`xpu + openvino`相当のvenv（pyannoteなし）で
  約4.9 GiB。実際のutteranプロファイル（pyannote等コア依存を含む）はこれより大きくなる
  見込みで、正確な値はStep 3の`setup.ps1`実機検証時に確定する。whisper.cppソース
  checkout（`.git`込み）は約188 MiB、ネイティブビルド成果物は`cpu`構成35 MiB、
  `vulkan`構成559 MiB（シェーダー生成物が大半）。全体では指示書見積り6〜8 GiBと
  大きく相反しない見込みだが、intel profile（xpu+openvino+whisper-cpp+pyannote）の
  最終確定値は未計測。
- **参考実装の所在訂正**: 指示書は参考実装を`_tmp/`と記載しているが、実際は
  `.tmp/TranscriptTool_v2-feature-gui-app.zip`に配置されていた
  （`.tmp/extracted/`へ展開して`native.py`／`transcription/selector.py`／
  `transcription/whisper_cpp.py`を確認）。
  - `native.py`の`_openvino_paths()`は`getattr(openvino, "get_cmake_path", None)`が
    `None`の場合`package_dir / "cmake"`へフォールバックする防御的実装だった。
    **実機のopenvino 2026.2.1には`get_cmake_path()`属性自体が存在しない**
    （`AttributeError: module 'openvino' has no attribute 'get_cmake_path'`）ことを確認。
    フォールバック先`<package_dir>/cmake/OpenVINOConfig.cmake`は実在したため、
    utteranの実装でも同じ防御的フォールバックを踏襲する（指示書の記述は
    `get_cmake_path()`を前提としており、実際にはフォールバック必須）。
  - OpenVINO構成のビルド自体も本機で実施し成功した（`-DWHISPER_OPENVINO=ON`、
    `-DOpenVINO_DIR=<venv>/site-packages/openvino/cmake`、38ターゲット、exit 0）。
    **実行時の重大な制約を確認**: ビルドした`whisper-cli.exe`は、OpenVINOランタイムDLL
    ディレクトリ（`<venv>/site-packages/openvino/libs`）がPATHに無い状態では
    `error while loading shared libraries: ggml.dll: cannot open shared object file`
    という誤解を招くエラーで起動不能（exit 127）になる。同ディレクトリをPATHへ追加すると
    正常起動する。これは指示書が要求する「実行時ライブラリのディレクトリを実行時に
    現在の環境から動的に解決する」設計（`_environment()`でPATH注入）が必須であることを
    実機で裏付けた。OpenVINO GPU encoderの実際の推論（`--ov-e-device GPU`）は、
    事前に`ggml-*-encoder-openvino.xml/.bin`（Phase 3bで実装するIR変換の成果物）が
    必要で、本調査時点では未変換のため`Could not open the file`で初期化失敗する
    ログを確認したのみ（クラッシュはせず、明確なエラーメッセージで失敗することを確認）。
    IR変換自体はPhase 3bの範囲。

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

### Phase 1/2受入試験（完了）

1. Windows環境・入力メタデータを本文非参照で記録し、派生クリップをoutput配下へ生成する。
2. 再開・個別再実行・timeout・process tree停止・出力抄録・peak memory対応ハーネスを作る。
3. G1〜G12を順番に実行し、各groupのresults/report/AISTATEをコミットする。
4. 不具合は失敗結果のtest commit後、回帰テスト・文書とともにfix commitし、再試験する。
5. 機能試験完了後だけcudaへ切替え、G13の2時間耐久試験を行う。
6. G14で集計、秘密／Git混入検査、profile判断、必須文書と最終報告を完了する。

### Phase 3a（実装完了、受入試験は次セッション）

1. Step 0事前調査（I-1〜I-7）を実機で実施し、`AISTATE.md`の専用節に記録する（最優先）。
2. Step 1: `pyproject.toml`のextrasをtorchビルド排他extras（cpu/cuda/xpu）と
   非排他extras（whisper-cpp/openvino/onnx）へ再編し、`uv lock`で検証する。
3. Step 2＋5基盤: `profiles.py`（venvレイアウト・既定プロファイル解決）と
   `native.py`（whisper.cppビルド機構）を実装し、モデル不要テストを追加する。
4. Step 3: `setup.ps1`を`-Profile`/`-List`/`-Remove`/`-SetDefault`対応へ全面改修する。
5. Step 4: `run.ps1`を新規作成し、`utteran profiles` CLIを接続し、`start.ps1`へ
   プロファイル管理メニューを追加する。
6. Step 5: `utteran native build/status/clean` CLIを接続する。
7. Step 6: `utteran devices`へプロファイル横断表示・Vulkan検出・ネイティブビルド状態を
   追加する（既存JSON構造は維持）。
8. 要件定義19/20章の新設と14章の更新、README／変更履歴／AISTATEの同期、
   cpu profileでの回帰確認（品質ゲート・実機native build）を行う。

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
- 受入G6完了: G6-01〜09全pass。models list/list --available/path/verify、非対話ID省略、
  曖昧ID候補、未登録ID、隔離cwd/cacheかつ空token環境でgated取得=exit2、隔離cacheの
  未取得OpenVINOモデルtranscribe=exit3と取得案内を確認。`.env`は未読・未変更。
  カタログにtiny等の小容量モデルがないため取得→検証→削除→再取得は条件不成立（該当なし）。
  既存large-v3-turbo/large-v3/Kotoba/community-1は削除・変更していない。次はG7。
- 受入G7完了: G7-01〜07全pass。CPU profile devices人間表示/JSONの主要9 key/auto最終値
  `faster-whisper/cpu/int8`、明示CPUの30秒実推論（32.344秒）、明示cuda:0とcuda:99の
  exit3、未知ASR backendのexit3を確認。CPU profileではGTX 1070 Tiは列挙されるが
  CTranslate2 cuDNN/cuBLASなし・PyTorch CPU buildのためautoはCPU。次はG8。
- 受入G8完了: G8-01〜07全pass。隔離config path/init/show、既存file上書き拒否、show JSON
  主要6 keyとtoken field非存在、`CLI > env > dotenv > TOML > defaults`の5段階、TOML内
  dummy `hf_token`の警告付き無視・値非表示、alignment.merge_gap 0.5→0.0で合成segment数
  1→2、不正beam_size=0のexit2・tracebackなしを確認。repo直下`.env`は未読・未変更。次はG9。
- 受入G9完了: G9-01〜11全pass。正常0、verbose+quiet不正2、PATH隔離ffmpeg不足3、
  cache隔離model不足3、cwd/token/keyring隔離話者分離token不足2、入力なし/empty/nonmedia 4、
  実Ctrl+C 130、batch部分失敗5を確認。全expected errorはactionableかつtracebackなし。
  synthetic unexpected RuntimeErrorは通常tracebackなし、--verboseのみtraceback/detailあり。次はG10。
- G10-01はdummy HF tokenを通常/verbose例外とJSON job logで原文非表示・`hf_****`化してpass。
  G10-02初回はH-004（受入scanner偽陽性）でfail: G8のdummy TOMLキー名`hf_token`を
  token値として検出した。failure commit後、実token相当の十分長いsuffixだけへ検出条件を絞る。
- H-004修正: byte scannerをword boundary付き`hf_` + 16文字以上のtoken suffixへ変更し、
  `hf_token` keyは非検出・長いdummy token値は検出するモデル不要回帰を追加。G10-02 rerun待ち。
- 受入G10完了: G10-01/02最新run全pass。dummy token原文は通常/verbose/例外/job logの
  いずれにも残らずmask済み。output配下125 filesをtoken形状scanして0件、`.env`・機密input・
  output resultsの3パスをgit check-ignore確認、git statusにもprivate内容なし。`.env`値自体は
  禁止事項に従い未読（標準HF token形状scanで代替）。WSL 88 passed/2 skipped、Windows 90 passed、
  ruff/format/mypy合格。次はG11。
- G11初回はG11-01/02ともH-005（文書受入器）でfail。CLI parameter introspectionが位置引数
  `input_path`をoption集合へ含め、README例runnerはexit2の例番号を報告せず原因特定不能。
  failure commit後、`--`始まりだけをoption比較し、README例番号/subcommandをfailureへ付与する。
  実文書のconfig field差分判定にはまだ到達していない。
- H-005修正後G11-02で文書不具合D-001を確定: `要件定義.md` 5.2 config例に実装済み
  `output.newline/show_speaker`と`alignment` 5 fieldが欠落。README transcribe option集合、
  general 5段階priority、token provider 3段階priority、終了code集合は一致。
- G11-01はH-006（Windows acceptance引数渡し）でfail: subprocess listが引用符なしの`**/*.wav`
  をdistlib launcherへ渡すとcwd全体へwildcard展開された。cmd.exeから明示引用したREADME同等commandは
  exit0を確認。failure commit後、受入器のglob値に引用符を保持して全exampleを再試験する。
- D-001修正: 要件定義5.2へoutput.newline/show_speakerとalignment 5 fieldを追加。
  READMEはtoken専用3 providerと一般設定5 sourceが別経路である旨を明記し、実装準拠の
  exit code 0/1/2/3/4/5/130表を追加。H-006はWindowsだけglob引用符を保持するよう修正。
- 受入G11完了: G11-01/02最新run pass。README CLI examples 27件をsafe fixture/isolated
  destructive targetで実行、transcribe 24 options、exit codes 7種、Config 30 fieldsをコード照合。
  setup/uv/quality例は先行実行記録も確認。WSL 88 passed/2 skipped、Windows 90 passed、
  ruff/format/mypy合格。次はG12 start.ps1。
- 受入G12完了: G12-01 pass。Windows PowerShell Parser API errors=0、start.ps1の32 command
  mappingをコード監査、Invoke-Expressionなし・argument array実行を確認。models list/devices JSON/
  jobs list/config pathのread-only対話メニューと、synthetic 30秒input指定のno-diarization/CPU/quiet
  transcribe dry-runをpipe入力でexit0確認。setup profile切替、Explorer起動、model/job削除確認は
  外部状態変更/UIのため未実施（コード監査のみ）。G1-G12終了、次は事前品質確認後G13 CUDA耐久。
- G13準備: Windows 90モデル不要tests、ruff/format/mypy合格後、`setup.ps1 -Profile cuda`成功。
  GTX 1070 Ti 8GiB、CTranslate2/PyTorch cuda:0実kernel、cuDNN/cuBLAS usable、auto=
  ASR cuda:0/int8 + diar cuda:0。最初の非対話setupはWSL継承PATHEXT=.CPLのためuv/pythonを
  認識せず、正規SHA検証経路でmanaged ffmpegを追加した後exit1。PATHEXTへ.EXE等を復元してrerun成功。
- G13 harness: WDDMでper-process VRAMがN/Aのためnvidia-smi GPU全体used/totalをbaseline/peak/delta
  として0.2秒pollへ追加（現在baseline約4.8GiB）。2時間実input duration=8363.883秒。
  ASR/diar model load時間はjob JSON logのstage開始→backend load完了差分で測る。test 3 passed/1 skip。
- 受入G13完了: G13-01〜08全pass。実inputは8363.861秒（ffprobe 8363.883秒）。
  - no diar CUDA: 417.734秒、RTF=20.02x、peak process-tree RAM 7,921,729,536 bytes
    (7.38GiB)、VRAM baseline 5,193,596,928 / peak 6,697,254,912 / delta 1,503,657,984
    bytes、ASR load 8.846秒。5形式、1934 segments、empty 0、duplicate max2、JP ratio .98063、
    coverage .623069、speaker0。
  - diar CUDA: 770.625秒、RTF=10.85x、peak RAM 7,909,371,904 bytes (7.37GiB)、VRAM
    baseline 5,200,936,960 / peak 7,850,688,512 / delta 2,649,751,552 bytes、ASR load
    8.186秒、diar load 14.740秒。5形式、output 1280 segments/5 labels、internal speakers4、
    regular turns2860/overlap pairs205、exclusive2758/mean2.165911秒、dominant .646736、
    UNKNOWN .000781、empty0、duplicate max2、JP .98063、coverage .651502。
  - 同一diar設定rerunは2.656秒で全stage skip。job `7be37b2d3fc10277`は保持。
  transcript本文/固有名はresults/docs/gitへ未記録。次はG14集計・報告・最終security scan。
- G14初回: 9ケース中8 pass、G14-05のみ受入器不具合H-007でfail。3分CUDA話者分離込みの
  JSON単独成果物に対し、`validate.py formats`へ未定義の`--extensions`を渡したためargparse
  exit 2となった。CUDA処理自体はexit 0で、他の成果物検証、CUDA auto選択、G0〜G13集約、
  2時間job保持、最終token scanはpass。指示書どおり失敗状態を先にcommitし、ケースを既存の
  `json` validatorへ修正してG14-05を再試験する。
- H-007修正: G14-05を既存の`json` validatorと話者数下限2の検証へ変更し、再試験はexit 0。
  G14-01〜09の最新結果は全pass。3分CUDAはASRのみ20.844秒、話者分離込み35.859秒。
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
- U-005: 受入完了後、利用者がstart.ps1の文字起こしウィザードで実会議MP4・kotoba-whisper-v2.0・
  CUDAを選択したところ、ASR実行直後にWindows終了コード`-1073741819`（STATUS_ACCESS_VIOLATION）で
  utteranプロセスが即死した。ジョブmanifestは`audio done/asr pending`で、Python例外もtracebackも
  一切出力されなかった。
- 30秒clip fixtureで同条件（faster-whisper/kotoba-whisper-v2.0/cuda:0/language ja/no-diarization）
  を隔離job/output先で再現し、実会議ファイル固有の問題ではなくモデル側の問題と切り分けた。
  導入済みモデルファイル一式（config.json/model.bin/tokenizer.json/vocabulary.json/
  preprocessor_config.json）はlarge-v3-turboと同型で、サイズも損傷を示す兆候はなかった。
- ローカルの`config.json`を確認したところ、`alignment_heads`が`[[7,0],[10,17],...,[25,6]]`と
  layer 25まで参照していた。HuggingFace上の変換元`kotoba-tech/kotoba-whisper-v2.0`本体の
  `generation_config.json`を確認すると、`decoder_layers=2`（distil-whisper系、encoderは32層を
  維持しdecoderのみ2層へ圧縮）に対し、同じ`alignment_heads`が既に上流でそのまま設定されていた。
  蒸留前の大型モデル（openai/whisper-large-v2相当）の値を更新せず配布した上流側の既存不具合。
  utteranは`pipeline.py`で単語タイムスタンプ（`word_timestamps=True`、JSON出力仕様上必須）を
  常時要求するため、実在しないdecoder layerへの範囲外アクセスで確実にクラッシュしていた。
  ローカルの`config.json`の`alignment_heads`を実在するlayer 1（decoderの最終層）の全20 headへ
  書き換えると、同じ再現手順がexit 0・単語タイムスタンプ含む正常なJSONで完了した。
- 恒久対応として、`models download`完了時と`models verify`実行時に、CTranslate2形式モデルへ
  隔離subprocess（`utteran.models._alignment_probe`）で単語タイムスタンプ計算を1回試行する
  検証を追加した。ネイティブクラッシュ由来の異常終了（0でも2でもないreturncode）を検知した
  場合のみ`alignment_heads`を安全な既定値`[[0, 0]]`へ自動修正する。Python例外（returncode 2）
  による検証不能時は誤って書き換えない。既に修正済み（`[[0, 0]]`）のモデルは再検証をスキップし、
  毎回のCPUモデルロードを避ける。`subprocess.run`をモックしたモデル不要回帰6件を追加。
  ruff/format/mypy/pytest（96 passed、既存の環境依存Ctrl+C harness試験1件は今回の変更と無関係に
  Git Bash端末実行時のみ失敗）で確認した。
- Phase 3a Step 1: `pyproject.toml`のextrasを`cpu`/`cuda`/`xpu`（`[tool.uv] conflicts`）と
  `whisper-cpp`/`openvino`/`onnx`（非排他）へ再編した。`xpu`は`triton-xpu`（Windows限定）を
  直接列挙しないと`uv lock`が失敗するというI-4の発見を反映した。`uv lock`で214 packages解決、
  `uv lock --check`合格、`uv sync --extra cpu --extra cuda`の同時指定拒否を実機確認した。
- Phase 3a Step 2＋5基盤: `src/utteran/profiles.py`（プロファイル定義、venvレイアウト
  `<venv_root>/<os>-<profile>`、`UTTERAN_VENV_DIR`/`[general].venv_dir`解決、既定プロファイル
  決定、`UTTERAN_PROFILE`読み取り）と`src/utteran/native.py`（whisper.cpp v1.9.1固定取得、
  cpu/openvino/vulkan/openvino_vulkan構成ビルド、前提条件probe、manifest記録、実行時
  ライブラリディレクトリ動的解決）を実装。`config.py`へ`venv_dir`/`native_dir`/
  `default_profile`を追加。モデル不要回帰32件（test_profiles.py 16件、test_native.py 16件）。
- 実機検証（native.py）: 実際にwhisper.cpp v1.9.1をclone、cpu/openvino/vulkan/openvino_vulkan
  の4構成すべてをビルドし成功（`utteran.native`経由、fakeでなく実cmake/git/glslc/OpenVINO）。
  `openvino_vulkan`実行ファイルを実際に起動し、OpenVINOパッケージを持つ環境からは
  正常完走（encode 83ms、CPU版の約9倍高速）、持たない環境からはDLL未検出で安全に失敗する
  ことを確認し、manifestに絶対パスを焼き込まない設計が機能することを実証した。
- 実機検証中に`native.py`の`ProcessRunner`が`subprocess.run(text=True)`のロケール依存
  デコード（cp932）でcmake/git出力の`UnicodeDecodeError`をsubprocess読み取りスレッド内で
  発生させ、出力が失われる不具合を発見。`encoding="utf-8", errors="replace"`明示指定で修正。
- Phase 3a Step 3: `setup.ps1`を全面改修。`-Profile`はプロファイル別`UV_PROJECT_ENVIRONMENT`で
  `uv sync`し、プロファイル別検証（cpu/cuda: 既存devices probe、intel: OpenVINO+torch XPU、
  vulkan: glslcビルド前提とvulkaninfoランタイムを別々に確認）を行う。`-List`/`-Remove -Yes`/
  `-SetDefault`（config.tomlへ`default_profile`をin-place書き込み）を追加。実機で
  `-List`・`-SetDefault cpu`・ダミープロファイルへの`-Remove`・`-Profile cpu`の冪等再実行
  （exit 0、devices probe合格）をすべて確認した。実装当初`uv sync`呼び出しに`sync`引数を
  二重指定するバグがあり、実機テストで発見・修正した。
- Phase 3a Step 4: `run.ps1`を新規追加。`-Profile`を名前付きパラメータとして`param()`で
  宣言すると、`-Profile`省略時にPowerShellの自動位置バインドが最初の裸引数
  （例:`transcribe`）を`$Profile`へ誤って束縛することを実機確認し、`$args`を手動解析する
  方式へ変更して回避した。既定プロファイル解決（config.toml既定→唯一の既存プロファイル→
  曖昧エラー）を実機で全パターン確認。`utteran profiles list/current/path`と
  `utteran native build/status/clean` CLIを追加し、モデル不要回帰10件を追加。
  `start.ps1`は同じ解決ロジックをプロファイル管理メニュー（一覧・セッション内選択・
  作成/更新・既定設定・削除）とともに実装し、関数を実プロジェクトディレクトリから
  単体で呼び出して動作確認した（対話ループ自体はこの環境がNonInteractiveのため
  Parser API検証とコード監査に留める。G12受入時と同じ扱い）。
- Phase 3a Step 6: `utteran devices`へ`profile`（現在プロファイル、他プロファイル一覧）、
  `vulkan`（build/runtime別）、`native`（manifest状態、構成別実行可否）を追加した
  （既存JSON構造は変更せず追加のみ）。実機で新フィールドの表示・JSON出力を確認した。
  モデル不要回帰3件を追加。
- 要件定義に19章「実行環境の分離」・20章「ネイティブビルド」を新設（本来の指示は「15./16.」
  だったが、本書には既に15〜18章が存在するため「既存章番号を変更せず末尾に追加する」という
  指示書の原則を優先し19/20とした）。14章をextras新構成へ更新。README/変更履歴/本ファイルを
  同期した。
- Phase 3a最終回帰確認（cpuプロファイル、実機・実モデル）: `faster-whisper:large-v3-turbo`
  （1.6 GiB）を`run.ps1 models download`で実取得し、`models verify`のalignment_heads
  プローブがokで完走することを確認。合成日本語音声（TTS生成、11.140秒）で
  `run.ps1 transcribe ... --no-diarization --format srt,vtt,json,txt,md`を実行し、
  5形式すべて生成・schema_version 1・speakers=[]・processing.diarization=nullを確認。
  同一コマンドの2回目実行は全5ステージが`ステージ再利用`となりexit 0で完走（レジューム動作）。
  `run.ps1 models list` / `jobs list` / `config show` / `devices`も実機で確認した。
  話者分離付き文字起こしは、本機にpyannote community-1の有効なgatedトークンがなく
  （`.env`の値は未参照、`default_token_provider().get_token()`がFalseを返すことのみ確認）、
  `models download pyannote:...`が想定どおり「トークン未設定」exit 2で失敗することを確認した
  にとどまる。pyannote.audio 4.0.7・torch 2.11.0+cpuはcpuプロファイルで正しくimport可能で
  `PyannoteBackend.is_available()`はTrueであり、コード経路自体は健全と判断した
  （実パイプライン実行はトークン制約により未検証）。
- 回帰確認中に2件の実行時不具合を発見・修正した（いずれもコミット前に自己検出）。
  1) `run.ps1`は`.ps1`スクリプトとして起動されるため、`--format srt,vtt,json,txt,md`のように
     引用符なしのカンマ区切り値を渡すと、PowerShellが呼び出し時点でバラの配列
     （`@("srt","vtt","json","txt","md")`）として解釈し、文字列化時に空白区切り
     （`"srt vtt json txt md"`）へ変わってしまうことを実機で発見した。同じ引数を
     `utteran.exe`へ直接渡した場合はこの問題が起きないため、`.ps1`起動特有の挙動である。
     `run.ps1`の引数解析へ、配列型要素を`,`で再結合してから引き渡す処理を追加して修正した。
  2) `run.ps1`・`start.ps1`はどちらも選択したプロファイルで`utteran`を起動する際に
     `UTTERAN_PROFILE`環境変数を子プロセスへ設定しておらず、`utteran profiles current`や
     `devices`の現在プロファイル表示が常に「不明」になっていた。両スクリプトの起動直前に
     `$env:UTTERAN_PROFILE`を設定するよう修正し、実機で正しく反映されることを確認した。

## 未解決の課題・保留事項

- 受入試験で確認された未修正の製品不具合はない。
- Phase 3受入試験P0〜P14はIntel実機で完了。pyannote community-1のCPU/XPU、固定／自動話者数、
  複数話者CPU/XPU完全一致、whisper.cpp 4構成、OpenVINO IR、結合、回帰、性能、耐久を検証済み。
- `cuda`profileは本機にNVIDIA GPUがないため、仕様どおりvenvを作成せずhardware不在の未検証として残す。
- P12の外部UI操作と対話入力は、Parser、動的option生成、command mapping、dry-run経路のコード監査まで。
- P14の実入力は仕様想定の約2時間20分ではなく24分46秒。存在する実ファイル全長では耐久合格した。
- 実文字起こし本文の意味的評価は利用者確認事項。構造・時刻・言語・重複・空欄・話者統計は合格。
- `device = "auto"`のまま複数プロファイルで同一ジョブを共有した場合、config_hashが
  実際に使用されるハードウェアの変化を検知できない制約（19.5節）は、レジューム機構への
  影響範囲を考慮し今回は解消せず、要件定義への明記のみで対応した。利用者が実運用で
  複数プロファイルを併用する場合は、`device`/`backend`の明示指定を推奨する。

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
- CTranslate2の`alignment_heads`不整合はネイティブクラッシュでPythonから捕捉不能なため、
  実プロセス内では検証できない。`models download`/`verify`から隔離subprocessで同じ処理を
  一度だけ試行し、プロセスの生死（exit code）という外部から観測可能な信号で判定する設計とした。
  クラッシュ確定時のみ書き換え、判定不能（Python例外）時は書き換えない非対称な扱いにしたのは、
  誤検知でモデルを不必要に壊すより、まれに古い壊れた設定を見逃す方を安全側と判断したため。
- Phase 3aのwhisper.cppバージョンは指示書既定のv1.8.6ではなくv1.9.1を採用した。実機で
  I-1〜I-3を検証した結果、v1.9.1がCPU/OpenVINO/Vulkan/OpenVINO+Vulkanの全構成で
  ビルド・実推論に成功したため。利用者に事前確認のうえ判断した。
- `cpu`/`cuda`/`xpu`extraのtriton-xpu依存は、uvの`explicit = true` indexが対象extraの
  `project.optional-dependencies`に直接列挙されたパッケージにしか適用されないという
  未文書化の制約（I-4）に対応するため、`triton-xpu`を`xpu` extraへ`sys_platform == 'win32'`
  条件付きで直接追加した。Linux版のtorch+xpu wheelも存在するが実機未検証のため、
  安全側でWindows限定のマーカーとした。
- `openvino.get_cmake_path()`は本機のopenvino 2026.2.1に存在しないため、
  参考実装と同じ`<package_dir>/cmake`へのフォールバック＋`OpenVINOConfig.cmake`存在確認を
  `native.py`に実装した。指示書は`get_cmake_path()`の存在を前提とした記述だったため、
  実機検証結果を優先しフォールバックを必須にした。
- Windowsのネイティブビルドは、Ninja生成器ではなくマルチコンフィグの
  `Visual Studio 17 2022`生成器を使う。実機検証でNinjaはコンパイラ環境
  （`vcvars64.bat`）を事前に読み込んでいないと構成に失敗する一方、VS生成器は
  vswhereと同じ機構でMSVCを自力検出できることを確認した。`utteran native build`が
  「Developer PowerShell」でなくても動作することを優先した。
- native.pyの`ProcessRunner`は`subprocess.run`に`text=True`ではなく明示的に
  `encoding="utf-8", errors="replace"`を指定する。日本語（cp932）ロケールのWindowsで
  `text=True`のロケール依存デコードがcmake/git出力の`UnicodeDecodeError`で出力を
  失う実障害を実機で確認したため。
- `run.ps1`は`-Profile`をparam()の名前付きパラメータとして宣言せず、`$args`を手動解析する。
  PowerShellの自動位置バインドは、`-Profile`省略時に最初の裸引数（`transcribe`等）を
  誤って`$Profile`へ束縛することを実機確認したため、この落とし穴を避ける設計とした。
- `device = "auto"`のままプロファイルを切り替えた場合のconfig_hash不変の制約（19.5節）は、
  ハッシュ計算ロジックを変更せず、要件定義への明記と利用者への明示指定の推奨だけで対応した。
  ジョブ・レジューム機構は受入試験済みの中核機能であり、変更に伴う回帰リスクが
  Phase 3aで解決すべき利益を上回ると判断したため。
- 要件定義の新章番号は指示書の「15./16.」ではなく19/20とした。本書には既に15〜18章が
  実在するため、「既存の章番号を変更せず末尾に追加する」という指示書自身の原則を、
  例示された章番号自体より優先した。

## 次に着手すべきこと

- `docs/受入試験報告_Phase3.md`の品質確認用成果物を、必要に応じて利用者が意味的に目視確認する。
- NVIDIA搭載機を利用できる場合だけ、hardware不在で対象外とした`cuda`profileを別途検証する。
- Phase 3は受入完了。次の開発作業は要件定義のPhase 4（長時間分割、メモリ監視、eval、CI、公開準備）。
- `output/_testdata/`と`output/_acceptance_p3/`は再試験用に保持。不要になった時点で手動削除できる。

## 既知の落とし穴・回避方法

- バックエンド固有オブジェクトを pipeline/exporter に渡さず、共通 dataclass へ変換する。
- Hugging Face トークンは config.toml から無視し、ログと例外をマスクする。
- pyannote.audio 4.x の出力 API の差異をバックエンド内部で吸収する。
- WindowsとWSLで同じ `.venv` を共有しない。Phase 3a以降はプロファイル別
  `.venvs/<os>-<profile>`がOS識別子でこれを恒久的に回避する。旧`.venv-windows`は
  互換のため残置しているが新規作成はしない。
- `start.ps1`/`setup.ps1`/`run.ps1`の先頭UTF-8 BOMを保持する。PowerShell 7だけで検査せず、
  Windows PowerShell 5.1 Parser APIと実行の双方を確認する。**Parser API
  （`[System.Management.Automation.(Language.)Parser]`）による静的構文検証だけでは
  BOM欠落によるこの種の実行時破損を検出できない**（Phase 3d R-4で`setup.ps1`/`run.ps1`の
  BOM欠落を発見した際、両ファイルは過去のParser API検査に合格し続けていた）。
  BOMを除去するformatterは使用しない。
- 第三者配布のCTranslate2モデル（特に蒸留系）は`config.json`の`alignment_heads`が
  decoder層数と整合しているとは限らない。単語タイムスタンプ計算はネイティブクラッシュとして
  失敗しうるため、モデル固有のバグを疑う際はまず`models verify`を実行する。
- `tools/acceptance`のCtrl+C系試験はWindows実コンソール（PowerShell/cmd.exe）前提。
  Git BashやConPTY経由で`pytest`を実行すると`test_ctrl_c_is_confined_to_the_child_console`が
  タイムアウトすることがある。製品側の不具合ではなく、試験ハーネスの実行環境依存。
- 日本語（cp932）ロケールのWindowsでは、上記と別にサブプロセスの`text=True`が
  ロケール依存デコードになり、Ctrl+C系以外のテスト（例:
  `test_interruptible_worker_hard_exit_returns_130_in_cli_process`）も、実行元コンソールの
  コードページによってどちらか一方だけがまれに失敗することがある（`native.py`で
  実際に踏んだ問題と同種）。製品コードでサブプロセス出力を扱う場合は`text=True`ではなく
  `encoding="utf-8", errors="replace"`を明示する。
- `cmake`をPATHに直接置くだけでなく、Visual Studio同梱のcmake
  （`...\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`）が使える。
  `utteran native build`はビルド前提としてcmakeの存在だけを確認し、由来は問わない。
- Vulkanの`glslc`（ビルド前提）と`vulkaninfo`（ランタイム）は独立した検出対象。
- whisper.cpp v1.9.1 Windows buildは非ASCIIのGGMLパスで0xC0000409終了しうる。backendは
  推論中だけGGML/IRをtemporaryへhardlinkするため、このstagingを外さない。
- PyTorch ONNX exporterは成功記号を出力するため、日本語cp932環境のconverter subprocessは
  `PYTHONIOENCODING=utf-8`必須。外すとIR変換がUnicodeEncodeErrorで失敗する。
  一方だけが利用可能な環境があるため、両方を確認しないと「ビルドできるが動かない」
  「動くがビルドできない」状態を見落とす。
- **PowerShellの`.ps1`スクリプトへ引用符なしのカンマ区切り値（例: `--format srt,vtt,json`）を
  渡すと、呼び出し時点でPowerShellがバラの配列として解釈し、文字列化時に空白区切りへ
  変わってしまう。** `utteran.exe`を直接呼ぶ場合はこの問題が起きない（.ps1起動特有）。
  `run.ps1`のような透過的な引数パススルーを書く場合は、各要素の型が`[array]`かどうかを
  確認し、配列なら`,`で再結合してから次へ渡すこと。`$args`をそのまま信用しない。
- `run.ps1` / `start.ps1`は、選択・解決したプロファイルで`utteran`を起動する直前に
  必ず`$env:UTTERAN_PROFILE`を設定すること。設定を忘れると`utteran profiles current`や
  `devices`の現在プロファイル表示が常に「不明」になる（実際に起きた不具合）。

## 動作確認環境・手順

- 作業パス: `<checkout>/Utteran`
- 受入実行環境: Windows 11 Pro 10.0.26200、Python 3.12.0、uv 0.11.32。
  補助品質検査はWSL Python 3.11.15でも実施。
- Git リポジトリ初回コミットは `83a4b29`。Phase 2 実装と指示書は
  `feat: implement Phase 2 operational workflows` の変更セットに収録。
- `python3 --version`: 3.12.3。
- ユーザー領域の `uv --version`: 0.12.1。
- WSL の `ffmpeg -version`: 未導入。Windows 側 `C:\path\ffmpeg\bin\ffmpeg.exe` は確認したが、
  WSL パス引数との相互運用制約があるため製品検証には一時 Linux 静的版を使用。
- ユーザー領域の `uv sync --extra dev`: 成功、59パッケージ導入、`uv.lock` 生成。
- `uv run pytest -m 'not requires_model' --capture=sys`: 基盤段階で 12 passed。
- pyannote.audio 4.0.7 の `Pipeline.from_pretrained` / `DiarizeOutput` を導入済みコードで確認。
- 現環境はシステム ffmpeg 共有ライブラリ未導入のため TorchCodec が警告する。waveform 渡しで回避。
- align 追加時: 7 tests passed、ruff passed、mypy strict passed（外部 typed package は追跡除外）。
- `uv sync --extra pyannote --extra dev --link-mode=copy`: 成功、pyannote.audio 4.0.7 導入確認。
- `uv sync --link-mode=copy` と直後の `uv sync --check`: 成功（extra なし49 packages）。
- WSL環境は`uv sync --extra dev --link-mode=copy`済み。
- WSL Python 3.11.15最終: `pytest -m "not requires_model"` = 90 passed / 2 Windows-only skipped。
- `uv run --no-sync ruff check`: All checks passed。
- `ruff format --check src tests tools`: 52 files already formatted。
- `uv run --no-sync mypy`: Success、30 source files。
- `uv run utteran --help` / `transcribe --help`、devices、models、jobs、config の主要 read-only
  コマンド: exit 0。
- 合成 MP4 + Linux ffmpeg: 正規化結果 mono / sample width 2 / 16kHz を確認。
- 合成 MP4 + cached faster-whisper tiny + device auto: CLI exit 0、CPU fallback、
  SRT/VTT/JSON/TXT/MD の5ファイルと JSON schema_version 1 を確認。
- Phase 2 実 E2E: 一時 Linux 静的 ffmpeg + 合成 MP4 + cached tiny + no-diarization。
  初回5段階実行、同一 job ID の2回目全 skip、format 変更時 export-only、force 時全段階実行を確認。
- Phase 2初期WSLの`utteran devices --json`: CUDAライブラリ不足を検出しauto=CPU/int8。
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
- Windows最終環境は`uv sync --extra cuda --extra dev`済み。Python 3.12.0でモデル不要92 passed、
  ruff check、52 files format check、mypy 30 source filesが合格。
- Windows最終profileは指示書の判断基準に従いCUDAを維持。PyTorch 2.11.0+cu126、
  `devices --json`のautoはASR=`cuda:0/int8`、話者分離=`cuda:0`。
- WSL/Windows双方でruff checkとmypy（30 source files）成功。mypyを両OSから同じ
  `.mypy_cache` へ同時実行するとinternal errorになったため、クロスOS検査は逐次実行する。
- `uv lock --check`: 成功、163 packages 解決済み。
- `git diff --check`: 問題なし。
- 受入最新集計: `output/_acceptance/results.jsonl`は162 records、115 unique ID、115 pass。
  最終秘密値走査は145 files、token形式match 0。`.env`内容は未読。
- G13保持job `output/_acceptance/jobs/7be37b2d3fc10277`は全stage done、約269 MiB。
  `output/_testdata`は58,314,597 bytesで、100 MiB上限未満のため再現用に保持。
- `要件定義.md` は Phase 1 設計書を基礎に、Phase 2 指示書の訂正5点と実効 output_dir の
  export hash 判断を同期済み。このため設計書原本との差分は意図した仕様更新。
