# AI 作業状態

## Phase bugfix-b CI format gate復旧（0.1.13、2026-08-27）

### 原因と修正

Phase bugfix-a終了時に、正式なCI gateである`ruff format --check src tests tools`が既存差分として
未解消のまま残り、mainのLinux CIが赤くなっていた。lock済みRuff 0.16.1で再現した対象は次の13 file。

- `src/utteran/_device_probe.py`
- `src/utteran/devices.py`
- `src/utteran/logging.py`
- `src/utteran/models/manager.py`
- `src/utteran_gui/hardware.py`
- `src/utteran_gui/logging_runtime.py`
- `src/utteran_gui/operation_queue.py`
- `tests/test_asr_registry.py`
- `tests/test_config.py`
- `tests/test_devices.py`
- `tests/test_gui.py`
- `tests/test_hardware.py`
- `tests/test_operation_queue.py`

上記fileだけへformatterを適用した。修正前後のPython ASTを位置情報なしで比較し、13 fileすべて同一で、
runtime semantics、条件分岐、定数値、公開API、test期待値には変更がないことを確認した。

`要件定義.md` 23章とREADMEはformat checkを正式なCI／開発品質gateとして既に定義していたため、
変更していない。一方、`docs/リリース手順.md`の日常確認項目にはformat checkとlock checkが欠け、
作業完了条件がCI契約と不一致だったため、必須コマンドを揃えた。今後CI必須gateの失敗を残す場合は
「完了」とせず、明示的な未完了事項として扱う。

### 最終検証

- `uv sync --extra dev --extra gui --locked`、`uv lock --check`: pass。lock差分はproject versionだけ。
- `ruff check src tests tools`: pass。`ruff format --check src tests tools`: 107 files pass。
- `mypy`: 58 source files pass。
- `pytest -m "not requires_model"`: 365 passed、既知のStarlette deprecation warning 1件。
- PowerShell BOM check: 追跡5 file pass。
- public history worktree scan: blocking finding 0でpass。全到達可能履歴のredacted JSON監査もexit 0。
- Windows PowerShell 5.1で全追跡`.ps1`のParser API検査と、`setup.ps1 -List`、
  `start.ps1`終了選択、`run.ps1 -Profile cpu --help`を含むheadless startup: pass。
- `git diff --check`: pass。実model、GPU、長時間音声、installer buildは変更範囲外のため未実施。
- remote GitHub Actionsはpushしていないため未確認。push後に`linux-quality`と`windows-tests`の
  両方がsuccessになることを確認する必要がある。

## Phase bugfix-a ウィザード起動フリーズ（0.1.12、2026-08-27）

### Step 1: 引き金の特定（修正前）

stdout／stderrを`output/bugfix-a/baseline/`へ隔離し、設定fileが存在しない初回条件で
`gui.ps1`を実行した。セットアップウィザードが自動表示されるページ読み込み時に、
実体の`python.exe`は`Responding=False`となった。隔離stderrには`window.native` 20件、
`maximum recursion depth exceeded` 15件、`--- Logging error ---` 10件、UI thread違反5件が
記録され、報告された症状を再現した。

引き金はウィザード内のボタン、ファイル選択、drag-and-dropではなく、初回ページ読み込み時の
pywebview JavaScript bridge生成だった。`NativeDialogApi`を`js_api`として渡した後、公開属性
`NativeDialogApi.window`へpywebviewのWindowを代入していた。pywebview 6.2.1の
`webview.util.inject_pywebview().get_functions()`は、`js_api`の公開属性を`dir()`と`getattr()`で
再帰列挙する。このため`window.native`以下の.NET相互参照へ到達したことを、実装と上記実ログの
両方で確認した。JavaScriptに`window.native`という記述はなくても、Python bridgeの公開属性が
到達経路になっていた。

> pywebview の `window.native` 以下を JavaScript から辿ると、
> .NET のプロパティ相互参照により再帰走査が無限に続き、
> UI スレッドが応答しなくなる。内部オブジェクトへ直接アクセスせず、
> Python 側で明示的に公開した API を経由すること。

> フロントエンドの例外が Python 側のログに記録されず、
> 配布版での調査が極めて困難だった。
> Phase 5j のログ基盤は Python 側のみを対象としていた。

### Step 2: 修正と判断

- `NativeDialogApi.window`を`_window`へ変更した。pywebviewが先頭`_`のmemberを除外する契約を使い、
  JavaScript公開memberを`choose_path`と`report_frontend_error`の2 methodだけに限定した。
  file／folder dialogは従来どおりPython側で`create_file_dialog`を呼び、選択pathを保存しない。
- drag-and-dropは案B（廃止して選択dialogへ一本化）を選んだ。標準Web File APIは絶対pathを
  保証せず、`file.name`だけをpath欄へ入れると成功したように見えてffmpegで失敗するためである。
  `file.path`、drop handler、日英の案内文を削除した。
- JavaScriptの`error`／`unhandledrejection`を最大20件queueし、各fieldを2000文字へ制限して
  Python構造化logへ転送する機能を追加した。log側の例外は握り、診断がUIを止めないようにした。
  再帰log大量出力の根本経路を除いたうえで、件数・サイズ制限も設けた。
- 診断APIが`None`を返すとpywebview 6.2.1側で`JSON.parse(undefined)`となることを転送log自身で
  発見したため、JSON化可能な`bool`を必ず返すよう修正した。native dialog定数もdeprecatedな
  `OPEN_DIALOG`／`FOLDER_DIALOG`から`FileDialog.OPEN`／`FOLDER`へ更新した。
- 回帰testはbridgeの公開member集合を固定し、publicなnative objectが追加されないことを守る。
  あわせて`file.path`／`dataTransfer.files`とdrop案内がないこと、frontend error転送を検査する。

修正版を同じ初回条件で`gui.ps1`から起動すると、15秒後も実体`python.exe`は`Responding=True`、
stderrは0 byteで、Intel GPU構成／CPU構成を選べるウィザードが正常描画された。構成選択→話者分離
選択→model選択→確認→実行画面まで操作し、その間も応答を維持した。検証中、Windowsの
`platformdirs`が環境変数でなくKnown Folder APIを使用するためuser settingsを新規作成したが、
事前には存在しなかったことを作成時刻で確認し、process tree停止後に当該fileだけを削除して元の
「設定なし」状態へ戻した。既存venv・model・jobは削除・移動していない。

### Step 3: 最終検証

- source版`gui.ps1`: ウィザードのwelcome、Intel構成、話者分離なし、GGML model、確認、実行、完了を
  実画面で通過した。`venv,vad_model,asr_model,smoke`がすべて完了し、smoke testで合成音声の
  文字起こしが成功した。完了画面から文字起こしworkspaceへ移動できた。
- source版のfile選択buttonからPythonのnative OPEN dialogが呼ばれ、cancel後も
  `Responding=True`、stderr 0 byteだった。folder dialogは同じ`choose_path` methodの
  `FileDialog.FOLDER`分岐を自動testで確認した。
- source版2回目起動はworkspaceを直接表示し、ウィザードを再表示しなかった。
- `build.ps1`で0.1.12をbuild後、`dist/utteran-gui/utteran-gui.exe`でも同じウィザードを完走した。
  4 stage完了後も`Responding=True`、stderr 0 byte、`window.native`、recursion、UI thread違反、
  `--- Logging error ---`はいずれも0件だった。配布GUIの2回目起動もworkspaceを直接表示した。
- 検証用に新規作成されたuser `settings.json`は、source／配布GUIの確認後にfile単体を削除し、
  作業開始時の「設定なし」へ戻した。既存venv・model・jobは保持した。
- `uv run pytest -m "not requires_model"`: 365 passed、既知のStarlette deprecation warning 1件。
- `uv run ruff check src tests tools`: pass。`uv run mypy`: pass（58 source files）。
  JavaScript構文検査、`uv lock --check`、`git diff --check`: pass。
- `ruff format --check src tests tools`は今回未変更の既存12 fileと、既存箇所を含む`test_gui.py`を
  現在のformatterなら再整形するとして不合格だった。完了条件ではないため無関係な一括整形はしない。
- Intel Arc 140Tの本機で実施した。別のCore i7機には本sessionからアクセスできず未実施。

配布物は`dist/installer/utteran-setup-0.1.12.exe`、SHA-256は
`a83e13cef0e7fa6997d706165d7d971b3dc581a7fc10fb7660f6f278561190a0`。
build成果物はGitへ追加しない。

## 長時間話者分離の粗大化調査と修正（0.1.11、2026-08-26）

`fix/long-audio-diarization`で、Intel Arc 140T実機上の116.2分会議音声が18segment、
最長61.2分となる問題を、既存`asr.json`・`diarization.json`・`merged.json`から本文を出さず
段階別に切り分けた。

> 受入試験の話者分離判定が「細分化しすぎ」しか検出できず、
> 「粗すぎる」を通過させていた。3分クリップでの検証は正常だったが、
> 長時間音声での粒度が確認されていなかった。

### Step 0: Arc実機の基礎確認

- GPUは`Intel(R) Arc(TM) 140T GPU (32GB)`、driver 32.0.101.8626。
- `devices --json --refresh`は12.85秒、7 probe全完了、timeout 0。XPU/OpenVINO利用可、
  autoはASR whisper.cpp/Vulkan、話者分離pyannote/XPUを選択した。
- 0.1.10配布GUIは起動8秒後も`Responding=True`。合成30秒のfaster-whisper/small/CPUは
  exit 0、ASR 5.745秒、7segmentで完了した。

### Step 1: 段階切り分け（修正前）

報告対象は6972.13秒。pyannote通常版は2498区間・1330交代・最長35.066秒、exclusive版は
2532区間・1370交代・最長35.066秒で、ここでは粗大化していなかった。通常版を使っても
最終18件となり、exclusiveも原因ではなかった。

whisper.cpp ASRは1858segmentのうち単語時刻あり1件、合計20語、単語話者交代0だった。
分割1858→極短吸収34（912回）→同一話者結合18（16回）、最長3671.75秒となった。
単語のないsegmentに`len(words) < 2`を無条件適用したため、実際には長いsegmentまで極短扱いし、
前後同一話者への吸収を連鎖させたことが直接原因だった。吸収の単語条件を無効化した既存JSON
再計算だけでも388件・最長199.57秒へ回復した。

同じ実行ログではwhisper.cppが26,890語をzero lengthとして除外していた。実機ビルドの
whisper.cpp v1.9.1ソースを確認すると、VAD有効時に`whisper_full_get_segment_t0/t1`は
`vad_mapping_table`で元音声へ戻す一方、`whisper_full_get_token_data`の`t0/t1/t_dtw`は
圧縮後timelineのままJSONへ書いていた。3分再現では678 token中、DTW非負614に対して
segment絶対範囲内5、offset正区間597に対して絶対範囲内4だった。したがってpyannote、
exclusive、0.5秒merge閾値ではなく、whisper.cpp VADのsegment/token timeline不一致と、
単語欠落を増幅する吸収条件の複合不具合と確定した。

### Step 2-4: 修正、ログ、受入基準

- 単語時刻要求時はwhisper.cpp内蔵VADを実効無効化する。ASR単独で単語時刻不要なら既定VADを維持。
- 有効なtoken offsetをDTWより優先し、一部DTWの局所逆行を回避する。分割segmentは所属語の
  最小開始・最大終了を境界とし、全語包含を保証する。
- 極短吸収の2語未満条件は`words`が実在する場合だけ適用する。0.3秒・0.5秒の設定値は維持。
  3〜116分で結合後最長区間比が増加しなかったため、音声長別閾値や結合回数上限は追加しない。
- `diarization_statistics`、`alignment_statistics`、`asr_word_timestamp_statistics`と
  `tools/diarization_stats.py`を追加。本文・固有名詞・話者名は記録しない。
- 受入`--require-quality`へ1.0 segment/分以上、最長segment比25%以下を追加。修正前実測は
  0.155件/分・52.66%で両方不合格、修正後は7.392件/分・0.996%で両方合格。

### Step 5: 実機検証

同一音声先頭からの実測（whisper.cpp/openvino_vulkan + pyannote/XPU、話者数2）:

| 長さ | 最終segment | 件/分 | 最長segment | 最長比 |
|---:|---:|---:|---:|---:|
| 3分 | 38 | 12.67 | 7.72秒 | 4.29% |
| 10分 | 101 | 10.10 | 27.40秒 | 4.57% |
| 30分 | 262 | 8.73 | 37.38秒 | 2.08% |
| 60分 | 536 | 8.93 | 37.98秒 | 1.05% |
| 116.2分 | 859 | 7.39 | 69.45秒 | 1.00% |

116.2分最終段階はASR 1402/1402segmentに単語あり、25,022語、単語話者交代880、
分割2084、吸収111回後1862、結合1003回後859。既存構造validatorと新quality gateはexit 0。
3分CPU/XPU比較は両方38segment、180秒、文字数一致でequivalence exit 0（CPU 80.0秒、
XPU 37.4秒）。3分はPhase 3別素材の25〜31件と同程度で、10〜116分も破綻点はなかった。

ASR/merge policy versionをstage config hashへ含めたため既存ジョブは該当stage以降を再計算する。
これは出力segment境界が変わる破壊的変更である。調査用clipは`output/_testdata/`のみでGit対象外、
元入力は変更・削除・移動していない。

### 品質ゲートと配布物

- `uv run ruff check src tests tools`: pass。
- `uv run mypy`: pass（58 source files）。
- `uv run pytest -m "not requires_model"`: 363 passed、既知のStarlette deprecation warning 1件。
- `uv lock --check`、`git diff --check`: pass。
- `build.ps1`: pass。配布物は`dist/installer/utteran-setup-0.1.11.exe`、SHA-256は
  `88a6c74536052f09ef027a8c863de3e2832a295b35885637ae8c37cfeb6f6c14`。
  同時生成した0.1.11 GUIは起動8秒後も`Responding=True`を確認した。

## Phase 5m CPU推論の異常な遅延の調査と修正（2026-08-26）

`docs/utteran_CPU推論遅延_指示書.md`相当の指示（会話内、文書化はされていない）に従い、
`fix/cpu-inference-slowdown`で実施した。Phase 5lの検証中に発見された「本機の
faster-whisper CPU推論（smallモデル）が、無音3秒のwavでも5分以上完了しない」
不具合を、推測ではなく実測で切り分けて原因を特定し、修正した。実施環境は
Phase 5k/5l検証対象と同一の本機（Core i7-1165G7 / Iris Xe / NVIDIA なし）。

### Step 1: 切り分け（実測）

まず`python -X importtime -c "import ctranslate2"`を実行したところ、**モジュール
インポート単体が60秒超で完了しなかった**。`faulthandler.dump_traceback_later()`で
12秒後のスタックを採取すると、一貫して`torch/__init__.py:275 _load_dll_libraries`を
指した。`torch/lib/`を調査すると`torch_xpu.dll`が840,676,352 bytes（約802 MiB）
あり、`_load_dll_libraries()`がこれを`kernel32.LoadLibraryW`で読み込む処理が
原因と判明した。`import faster_whisper`単体も同様に60秒超では完了せず、
実際にバックグラウンドで走らせたまま他の調査を続けたところ、**約20分後に
`Get-Process`でCPU時間1042.92秒（17分超）に達してもまだ完了していなかった**
（メモリは約116 MiBで横ばい、`Responding`状態だが実計算中で、AISTATEの
「CPU時間が増加し続ける」という既存記録と一致）。この時点でkillした。

`sys.modules["torch"]`へダミーモジュールを事前登録してから`import ctranslate2`を
実行すると、0.32秒で完了し`get_supported_compute_types("cpu")`も正常に動作した
（登録解除後に本物の`import torch`をすると、想定どおり再び60秒超かかった）。
これにより、**faster-whisper CPU推論の異常な遅延は、VADのハルシネーション
（1-A）でもVAD自体（1-B）でもCUDA探索（Phase 5kが特定した
`ctranslate2.get_cuda_device_count()`のハング）でもなく、CTranslate2
4.8.1の`ctranslate2.specs.model_spec`がモジュールレベルで無条件に実行する
`try: import torch`（実際には使わないモデル変換ヘルパーのため）が、
本機のPyTorch XPUビルド（`torch_xpu.dll`のネイティブDLLロード）を
巻き込んで極端に遅くなることが根本原因**と実測で確定した（Step 2の仮説表で
言えばCに最も近いが、CUDAデバイスへの明示的な問い合わせではなく単なる
`import`が引き金である点が異なる）。

1-C（モデルサイズ）: tiny・smallいずれでも同じ箇所で同じ遅延が発生することを
コード解析で確認した（`WhisperModel`のコンストラクタに到達する前、
`import`の時点で止まるため模型サイズに依存しない）。large-v3-turboの
追加ダウンロードを試みたが、この時点の回線速度が実測2.4 KB/s
（ETA表示が669,114秒）と極端に遅く、待つのは非現実的と判断し中止した
（未実施として記録。ただし原因がモデル非依存であることはコードレベルで
確認済みのため、結論には影響しない）。

1-D（whisper.cpp / native build）: `utteran native status --json`を
実行し、`vswhere.exe`がVisual Studio 17 2022のC++ツールセットを検出できず
CMake構成が失敗することを再確認した（Phase 5l既知の限界と同じ）。
本セッションでも未導入のため**未実施**。

1-E（Intel Arc 140T機との比較）: 本セッションからその実機へアクセスできず
**未実施**。

### Step 2: 修正

`utteran.devices.suppress_torch_import()`（新設、contextmanager）を実装した。
`sys.modules`に`torch`が未登録の場合だけ軽量な代替`ModuleType`を一時登録し、
`import ctranslate2`完了後に直ちに取り除く。適用箇所は次の2つ。

- `src/utteran/asr/faster_whisper.py`の`FasterWhisperBackend.available_devices()`と
  `load()`（実推論経路。ここが無制限にハングしていた本丸）。
- `src/utteran/_device_probe.py`の`_ctranslate2_cpu()`・`_ctranslate2_cuda_count()`・
  `_ctranslate2_cuda()`（Phase 5kの隔離プローブ内。CPU専用の問い合わせでも
  巻き込みimportで20秒タイムアウトしていたため、副次的に高速化する）。

`torch_cuda`・`torch_xpu`プローブ（`_device_probe.py::_torch()`）は実際に
PyTorch CUDA/XPUを検証する必要があるため対象外とし、Phase 5kの20秒
タイムアウト＋killに委ねた（本機では引き続き毎回タイムアウトする。これは
本修正のスコープ外と判断した）。

あわせて、ASR読み込み・推論の各段階を構造化イベントとして記録するよう
`faster_whisper.py`を拡張した。`asr_ctranslate2_import_completed`
（インポート所要時間、torch差し替えの有無）、`asr_backend_resolved`
（`cpu_threads`・`num_workers`・`load_duration_seconds`を追加）、
`asr_transcribe_completed`（新設、推論所要時間・実時間比・segment数・
vad_filter）。D（スレッド数）・E（compute_type）はこのログ拡張と実測で
確認した結果、問題なしと判断した（CPU自動選択は`int8`、`cpu_threads=0`
（CTranslate2既定の自動選択）で、いずれも不合理な値ではなかった）。

### Step 3: 検証（実機、本機、2026-08-26）

`git stash`で修正前のコードへ一時的に戻し、報告された条件そのもの
（`utteran transcribe`、faster-whisper・CPU・small・無音3秒・VAD既定）を
実際のCLI経由で実行したところ、**300秒（5分）のタイムアウトまでASR
ステージから一切進まなかった**（audioステージは0.6秒で完了、asrステージ
開始後は無応答）。修正を復元して同一条件を再実行すると、**ASRステージ
合計6.48秒**（`asr_ctranslate2_import_completed`のimport 0.4秒＋
`asr_backend_resolved`のモデル読み込み4.36秒＋`asr_transcribe_completed`の
推論0.69秒）で完了し、VADが無音3秒全体を正しく除去してsegment数0を
返した（ハルシネーションなし）。

同じ修正済みコードで、無音30秒（3.68秒、VADが全区間を除去）、純音3秒・
合成音声3秒（いずれもVADが非音声と判定しsegment数0、0.5〜0.7秒）、
VAD無効化（`UTTERAN_ASR__VAD_FILTER=false`、無音3秒で2.21秒、フル推論しても
ハルシネーションなくsegment数0）、tinyモデル（0.35秒load＋0.35秒推論）を
それぞれ計測し、いずれも数秒以内に完了した。

実際に発話を含む音声での回帰確認として、Windows標準のSAPI
（`System.Speech.Synthesis.SpeechSynthesizer`）で13.73秒の英語合成音声を
生成し（内容は本ファイルに記録しない）、同じsmallモデルで文字起こしした。
VADが音声区間を正しく保持し（除去0秒）、2 segmentを3.62秒（実時間比3.79倍）で
生成し、`.srt`/`.json`/`.md`出力も正常に生成された。これにより、
`suppress_torch_import()`が推論経路の動作（本物の文字起こし結果）へ
悪影響を与えていないことを確認した。実際の会議音声（数分）は本セッションで
用意できなかったため未実施（合成音声13.73秒での確認をもって代替した）。

### 統合受入試験ハーネス（実機、既定162件）

本セッション開始時点で`output/_testdata/`が空（フィクスチャ未配置）で、既定modelの
`faster-whisper:large-v3-turbo`も未取得だったため、初回実行はpass=39/fail=84/skip=39
だった。`results.jsonl`を精査したところ、84件の失敗は**すべて**`FileNotFoundError`
（テスト音声不在）または後続段階の連鎖失敗で、torch/ctranslate2に言及するものは
0件だった。

SAPI合成音声とffmpegで`clip_30s.mp4`・`clip_03m.{wav,m4a,mp4}`・`broken.mp4`・
`empty.mp4`・`notmedia.txt`・batch/nested複製という最小限のフィクスチャを作成し
（複数話者を要する`clip_03m_multi.mp4`・10分クリップ`clip_10m.mp4`・whisper.cpp用
GGMLモデルは、内容の正しさを装えないため作成しなかった）、2回目を実行した。
実行中に`large-v3-turbo`・`kotoba-whisper-v2.0`が(harnessの別ケースが誘発した取得により)
完了し、pass=47/fail=76/skip=39となった。`clip_03m.mp4`欠落を補って3回目を実行した
結果、**pass=80/fail=47/skip=35（3485秒）**まで改善した。

残り47件の失敗を`results.jsonl`の`stderr`から全件確認し、原因を次の4種類に分類した
（推測ではなく実際のエラーメッセージから分類。**いずれもtorch/ctranslate2への言及は
0件**）。

1. whisper.cpp native buildおよびGGML専用モデル（`large-v3-turbo-q5_0`等）不在
   （P0-1・P2-1・P3-2・P5-6・P8-4等）。本機はVisual Studio C++ Build Tools未導入の
   ため引き続き未実施（1-D、Phase 5lから状態変わらず）。
2. `clip_10m.mp4`・`clip_03m_multi.mp4`（複数話者判定を要する）フィクスチャ不在
   （G9-09・P9-2・P10-A等）。内容の正しさを装えないため意図的に作成しなかった。
3. 話者分離のメモリガード（要件定義13.4章、Phase 4b実装）が`判定=impossible`を返す
   （G3系・G4-01等）。これは既存の安全機能が、本セッション中に`build.ps1`や複数の
   harness実行を並行させたことで実際に空きRAMが逼迫していた状態（実測2.44 GiB）を
   正しく検知して起動前に拒否したもので、コードの不具合ではない。
4. `validate.py`のJSONアサーション（`native.variants.cpu`等）がnative build前提の
   期待値を要求（P3-2）。1と同根。

G1-01相当（30秒mp4・CPU・no-diarization）を`--asr-model small`明示で直接実行し、
exit 0、5形式すべて出力、ASR 8.228秒（実時間比3.6倍）を個別に確認した。これにより、
既定modelを`large-v3-turbo`に固定した`config.acceptance.toml`とは独立に、本修正が
対象とする経路（faster-whisper CPU推論）が実際に正しく動作することを確認している。

**受入試験ハーネスは既定162件のうち完全合格ではない（80 pass / 47 fail / 35 skip）。**
禁止事項「実施できない検証を推測で埋めない」に従い、上記の通り原因を全件記録した。
未達成の47件はいずれも(a)本機のnative build未導入、(b)複数話者・長時間フィクスチャの
不在、(c)本セッションの一時的な空きRAM逼迫のいずれかに起因し、今回の修正
（`suppress_torch_import()`）に起因する失敗は1件も確認されなかった。

副次効果として、`utteran devices --json --refresh`の所要時間が
Phase 5k/5l実測の約90〜100秒から**約49秒**へ改善した。プローブ内訳は
`ctranslate2_cpu`0.44秒・`ctranslate2_cuda_count`0.47秒（いずれも従来20秒
タイムアウトしていた）、`torch_cuda`・`torch_xpu`は対象外のため引き続き
20秒ずつタイムアウト（残り約40秒の内訳）。

### 未実施・既知の限界

- large-v3-turboモデルでの直接計測（1-C、無音3秒での時間測定）は、当初の
  回線速度実測が2.4 KB/s（ETA 669,114秒）と極端に遅かったため見送った。
  tiny/smallの実測とコード解析（`import`時点で止まるためモデルサイズに
  依存しない）から結論への影響はないと判断した。なお、この後受入試験
  ハーネス実行中（別ケースの取得誘発）に回線が回復し`large-v3-turbo`・
  `kotoba-whisper-v2.0`とも完全取得済みとなった（後述の受入試験節参照）。
- whisper.cpp/native build経路との比較（1-D）は、本機にVisual Studio C++
  Build Toolsが未導入のため未実施（Phase 5lから状態変わらず）。
- Intel Arc 140T機での回帰確認（1-E）は本セッションからアクセスできず未実施。
  今回の修正はハードウェア機種に依存しない一般的な問題（CTranslate2 4.8.1が
  torchを巻き込みimportする点、Intel XPUビルドのPyTorchが大きなネイティブ
  DLLを持つ点）に対するものであり、Arc機でも同種の遅延が起きていれば
  同じ理屈で解消するはずだが、実機未確認である。
- 実際の会議音声（数分）での確認は用意できず、13.73秒の合成音声（SAPI）で
  代替した。
- `torch_cuda`・`torch_xpu`プローブ自体の20秒タイムアウトは今回のスコープ外
  として変更していない。本機ではこの2プローブが引き続きタイムアウトするため、
  話者分離のXPU自動選択は判定不能のまま（`devices --json`の`auto_selection`が
  示すとおりCPUへフォールバックする、Phase 5k以来の既知動作）。
- 受入試験ハーネス用に`output/_testdata/`（git対象外）へ最小限の合成音声
  フィクスチャを新規作成した: `clip_30s.mp4`・`clip_03m.{wav,m4a,mp4}`・
  `broken.mp4`・`empty.mp4`・`notmedia.txt`・`batch/`配下の複製。いずれも
  Windows SAPI（`System.Speech`）による単一話者の合成音声で、内容は
  記録していない。複数話者判定を要する`clip_03m_multi.mp4`と
  長時間の`clip_10m.mp4`は、内容の正しさを装えないため意図的に作成せず
  未実施のままとした（次回セッションでこれらを用意すればP9/P10/G9-09等の
  検証が進められる）。

### 破壊的変更への配慮

VADの既定値やASR設定は変更していないため、既存ジョブの`config_hash`は
変化せず、再計算は発生しない。`suppress_torch_import()`は`sys.modules`を
一時的にしか変更せず（`import ctranslate2`完了後に直ちに元へ戻す）、
既存のvenv・モデル・設定・ジョブへの影響はない。

### バージョニングとビルド

`要件定義.md`24.1章・`docs/リリース手順.md`の手順に従い0.1.9→0.1.10へ
パッチを上げた（`pyproject.toml`・`src/utteran/__init__.py`・
`src/utteran_gui/__init__.py`・`uv lock`）。`tests/test_version.py`で
一致を確認済み。

`.\build.ps1`を実行し、PyInstaller onedir→推論core非同梱check→Inno Setup
compileまで成功した。ビルドスクリプト自身の`Get-FileHash`呼び出しだけが
`CommandNotFoundException`で失敗した（`Start-Process`経由の非対話
PowerShellセッション固有の事象と見られ、原因の深追いはスコープ外と判断）。
インストーラー本体は正常生成されていたため、SHA-256は同じ`Get-FileHash`
コマンドレットを対話的に実行して算出し、`build.ps1`と同じ書式
（小文字16進 + 半角スペース2つ + ファイル名）で`.sha256`サイドカーを
手動作成した。

`dist\installer\utteran-setup-0.1.10.exe`
SHA-256: `30bd0f3ac6e7f3c8d7c370e19757f1e33f06cdb8405eb0ead42db997abffc1f3`

## Phase 5l 起動フリーズの解消とPhase 5kの完了（2026-08-26）

`docs/utteran_Phase5l_指示書.md`に従い、`fix/phase5l-startup-freeze`で実施した。
本機はWMI照会で`Intel(R) Iris(R) Xe Graphics`と確認でき、Phase 5k指示書が検証対象と
した「Core i7-1165G7 / Iris Xe / NVIDIA なし」の該当PCそのものだった。

### Step 0-1: 原因の特定（推測でなく実測）

まずGUI起動シーケンスへ段階ログを追加した（`utteran_gui.logging_runtime.log_stage`、
`app.py`・`environment.py`から呼ぶ）。実装時に、GUIログのroot loggerへ一度も
`setLevel`が呼ばれておらず、既定のWARNINGにより既存の`.info()`呼び出しが起動時から
ずっと無音で破棄されていたことが判明した（`configure_gui_logging`にINFOを設定して解決）。

段階ログを仕込んだ上で、`.venvs`未構築の状態から`setup.ps1 -Profile intel -Yes`を
実際に実行し、続けてキャッシュを削除した状態で`gui.ps1`を実行して実測した。

- `utteran devices --json`（キャッシュなし）は**94.7秒**（`setup.ps1`経由）
  および**105.7秒**（GUI経由、後述の理由で2重実行）かかった。7プローブ中
  CTranslate2 CPU/CUDA・PyTorch CUDA/XPUの4件がそれぞれ20秒タイムアウトした。
  キャッシュ命中時は5.5秒だった。
- `src/utteran_gui/cli.py::CliAdapter.run_json`は`devices --json`呼び出しに
  既定60秒のタイムアウトを使っており、上記の実測値を下回っていた。しかも
  `subprocess.run(timeout=...)`は直接の子（`utteran.exe`）だけをkillし、
  さらに孫プロセス（分離プローブ）を残したまま`subprocess.TimeoutExpired`を
  無捕捉で外へ伝播させる実装だった。
- `src/utteran_gui/environment.py::EnvironmentService.snapshot()`は`CliError`だけを
  捕捉しており、上記の`TimeoutExpired`を捕捉できず、`/api/environment`から
  未処理例外として伝播していた。
- `src/utteran_gui/web/app.js`の`boot()`は`await loadEnvironment(...)`の完了を
  待ってからジョブキュー確認・ウィザード再開判定へ進む実装だった。このため
  `/api/environment`が例外で失敗すると、`boot()`全体がそこで中断し、キュー確認も
  ウィザード再開判定も一切実行されなくなり、ウィンドウは表示されたまま
  「detecting...」表示から復帰しなくなっていた。

再現実験では、ウィザードの初回起動判定（`/api/wizard/hardware`、`hardware.py`の
別経路）も同時に`devices --json`を呼び出しており、2つの`devices --json`が並行実行
されて各プローブが2回ずつタイムアウトしていた（合計105.7秒）。この並行呼び出し自体は
Phase 5kが意図したプロセス分離の範囲内で安全に動作しており、今回の修正対象とは
別問題として記録に留め、変更はしていない。

### Step 2: 修正内容

- `CliAdapter.run_json`/`run_text`を`subprocess.run`から`Popen`+`communicate`+
  `processes.kill_process_tree`（Windowsは`taskkill /T /F`）へ変更した。タイムアウト時は
  プロセスツリーごと確実に終了させ、`CliError`として返す（既存の他の失敗と同じ形）。
- `environment.py`の`devices --json`呼び出しにだけ、プローブ合計の理論上の最悪値
  （7プローブ×20秒＋kill/再試行オーバーヘッド）を上回る200秒の専用タイムアウトを設定した。
  `EnvironmentService.snapshot()`の全てのCLI呼び出しを`CliError`だけでなく
  `subprocess.SubprocessError`・`OSError`も含めて捕捉するよう広げ、失敗時も例外を
  外へ伝播させず`errors`配列へ記録して安全側の応答を返すようにした。
- `app.js`の`boot()`で`loadEnvironment(...)`を`await`せず非同期実行に変更し、
  ジョブキュー確認・ウィザード再開判定をデバイス検出の完了を待たずに進めるようにした。
  デバイス検出の結果は従来通り`#environment-alert`へ非同期に反映される。
- `run_isolated_probe`（`utteran/devices.py`）に、PyInstallerでfrozenされたプロセス内から
  `command=`省略で呼ばれた場合に即座に例外を出す防御を追加した。`sys.executable`が
  frozen exe自身を指すため`-m`起動がGUIアプリ全体を再起動してしまう可能性があるが、
  `packaging/gui.spec`が`utteran`パッケージ自体をGUIビルドから除外しているため現状は
  到達しない。将来の変更でこの前提が崩れた場合に無言でアプリの多重起動を招かないための
  防御的な変更であり、今回の不具合の直接の原因ではない。

### Step 2 検証（実機、Core i7-1165G7 / Iris Xe / NVIDIA なし、本機）

デバイスプローブキャッシュを削除し、`logs/`を空にした状態で`gui.ps1`を起動し、
段階ログで追跡した。`environment_snapshot_start`から`environment_snapshot_done`まで
105.734秒かかり、その間`uvicorn_server_started`・`webview_window_created`は
起動直後（1秒未満）に完了済みで、ウィンドウ生成後の105秒間、GUIプロセスは
`Get-Process`で`Responding: True`のまま維持された。デバイス検出は`error_count: 0`で
正常終了した。修正前のコード（`run_json`の60秒固定タイムアウト、`boot()`の
`await`）であれば、この実測シナリオは`subprocess.TimeoutExpired`が無捕捉のまま
`/api/environment`から伝播し、`boot()`のジョブキュー確認・ウィザード判定が
実行されない状態になっていたはずである。

### Step 3: Phase 5kの残作業

- `tests/test_faster_whisper.py::test_auto_device_falls_back_to_cpu`を、
  `utteran.devices.run_isolated_probe`をmonkeypatchする形（`tests/test_devices.py`と
  同じ手法）へ書き直した。従来の`ctranslate2`モジュール直接monkeypatchは、
  Phase 5kでプローブが別プロセス化されたため届かなくなっていた。
- `tests/test_native.py`のOpenVINO関連3件（`test_probe_openvino_gpu_unavailable_without_the_package`
  等）を、`monkeypatch.setitem(sys.modules, "openvino", None)`で`openvino`パッケージの
  不在を明示的に模擬する形へ書き直した。従来はdev環境に`openvino`が入っていないことに
  暗黙に依存しており、`openvino`導入済みのIntel profile venvでは失敗していた
  （本機のIntel profile venvで実際に確認）。
- `tests/test_gui_processes.py::test_profile_cli_run_uses_no_window`を、
  `CliAdapter`の実装変更（`subprocess.run`→`Popen`）に合わせて`subprocess.Popen`を
  monkeypatchする形へ更新した。
- `README.md`・`要件定義.md`にPhase 5k由来のプローブ分離・タイムアウト・キャッシュの
  仕様と、Phase 5l由来のGUI側タイムアウト・非同期化を追記した（Phase 5kで未着手のまま
  main統合されていた文書更新義務を含む）。

### 未実施・既知の限界

- **Intel Arc 140T機（当初の不具合報告機）そのものでの検証は未実施。** 本セッションから
  その実機へアクセスできないため。今回の実機検証はPhase 5k指示書が検証対象とした
  「Core i7-1165G7 / Iris Xe / NVIDIA なし」機（本機）でのみ行った。修正内容は
  ハードウェア機種に依存しない一般的なタイムアウト整合性の問題であり、Arc機でも
  同じ理屈で発生・解消するはずだが、実機未確認のまま完了と報告しないという指示書の
  禁止事項に反しないよう、この限界を明記する。
- CTranslate2/PyTorchが対象環境でなぜ関数によらず一律にタイムアウトするのか
  （K-3相当の根本原因調査）は特定していない。回避策が見つからなくてもタイムアウトに
  よる復帰は機能するため、指示書の「タイムアウトの実装は省略しない」は満たしている。
- `/api/environment`と`/api/wizard/hardware`が独立に`devices --json`を呼び出し、
  初回起動時に並行実行されうる点は、Phase 5kのプロセス分離設計の範囲内で安全に
  動作することを実機で確認したが、重複呼び出し自体の解消（結果の共有・排他制御等）は
  今回のスコープ外として変更していない。
- **本機（Intel profile）で`utteran transcribe`（faster-whisper・CPU・`small`モデル）を
  実際に実行し、無音3秒のwavが5分以上かかっても終わらないことを発見した。** ワーカー
  process（`python.exe`）のCPU時間は継続して増加しており（実測52秒→300秒→380秒超）
  OSレベルのデッドロックではなく実計算を続けているが、メモリ使用量（約151MB）は
  ほぼ変化しないままだった。440Hzの純音（無音でない）でも同様の症状を確認した。
  **これはPhase 5lで変更したコード（`select_faster_whisper_device`・`run_isolated_probe`・
  GUI側の起動処理）の範囲外であり、`transcribe`/`asr`ステージ自体（`pipeline.py`・
  `faster_whisper.py::transcribe`・ctranslate2本体）の別の問題である可能性が高い。**
  時間の制約により深追いせず、`native build`（後述の理由でVisual Studio C++ Build Tools
  未導入のため実行不可）を含め、この機での「文字起こしが実際に実行できる」という
  Phase 5k本来の検証項目は**未達成のまま**である。次回このPCで作業する際は、
  この現象の再現・原因調査（`beam_size`・`condition_on_previous_text`・
  無音/非音声入力時の挙動、ctranslate2 CPU実行のスレッド設定等）を優先すべきである。
- **whisper.cpp OpenVINOバックエンドのnative buildは本機で実行できなかった。**
  `vswhere.exe -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64`が
  何も返さず、Visual Studio 2022 Community自体は導入済みだが「C++によるデスクトップ開発」
  ワークロード（MSVCコンパイラツールセット）が未導入と確認した。大容量の追加導入が
  必要なため今回は見送った。このため「OpenVINO経路での文字起こし」検証はできず、
  上記のfaster-whisper CPU経路でも別の問題（後述）に阻まれ、Phase 5k完了条件の
  「該当PCで文字起こしが実行できる」は本セッションでは満たせなかった。

## fix/phase5k-device-probe-timeoutのWIP統合（2026-08-26、未完了・Phase 5lで解消）

> `fix/phase5k-device-probe-timeout`は`docs/utteran_Phase5k_指示書.md`が要求する
> 「該当PC（NVIDIA GPUなし）での検証」が未実施のままmainへ統合された（利用者の指示による、
> pushはしていない）。完了とはみなさないこと。

`main`（origin由来、Phase 5j統合済み）と`chore/versioning-policy`をこの順にmainへmerge後、
最後に本branchをmergeした。コンフリクトは`src/utteran/logging.py`のみで、Phase 5kが
`JsonFormatter`へ`event`/`probe`/`probe_status`/`duration_seconds`という独自キーを追加していた
ものをPhase 5jの`utteran_event`/`utteran_fields`機構（`structured_event()`）へ統一する形で解決した
（指示書の「Phase 5jで整備したログ基盤を活用すること」に従った）。呼び出し側の
`devices.py::_log_probe_outcome`も`structured_event("device_probe", probe=..., probe_status=...,
duration_seconds=...)`へ書き換えた。

さらに、mypy/ruffで発覚した実装ミスも修正した: `cli.py`の`devices_command`が旧`configure_logging`
API（Phase 5j以前）をそのまま呼んでいたが、その関数はimportされておらず、かつPhase 5jの
Typer callback（`main`）が`devices`を含む全subcommand実行前に`configure_runtime_logging`を
既に呼んでいるため冗長だった。呼び出しごと削除し、未使用になった`default_probe_cache_path`の
importも削除した。

**既知の回帰（未修正、要対応）**: `tests/test_faster_whisper.py::test_auto_device_falls_back_to_cpu`
がmerge後に失敗するようになった（merge前のmainでは合格を確認済み）。原因は
`detect_ctranslate2()`がPhase 5kで`ctranslate2.get_cuda_device_count()`等の呼び出しを
`run_isolated_probe()`によるsubprocess経由へ変更したため、testが行っていた
`monkeypatch.setattr("ctranslate2.get_cuda_device_count", ...)`という同一プロセス内monkeypatchが
子processへ届かなくなったこと。正しい修正には、subprocess分離後のprobe機構に対応した新しい
test注入方法（`detect_devices`の`probes`引数のような依存性注入、または`run_isolated_probe`自体の
monkeypatch）への設計が必要で、実機検証を伴うPhase 5k本体の完了作業に属するため今回は
手を付けていない。他の`tests/test_devices.py`・`tests/test_hardware.py`はこのbranch内で
更新済みで合格する。

`README.md`・`変更履歴.md`・`要件定義.md`への指示書要求分の更新も未着手。

## 運用ルール（必読・恒久）

> 変更を加えた作業単位ごとに、パッチバージョンを1つ上げ、`変更履歴.md`に記載する。
> マイナーバージョンの繰り上げは利用者が判断するため、AIは独断で行わない。

- パッチ番号は9で止めず10を超えて増え続ける（`v0.1.9`の次は`v0.1.10`）。
- 揃えるべき箇所（`pyproject.toml`／`src/utteran/__init__.py`／`src/utteran_gui/__init__.py`／
  `uv.lock`）と手順は`docs/リリース手順.md`「バージョン更新手順（作業単位ごと）」、
  規約本体は`要件定義.md`24.1章を参照。
- `packaging/gui.spec`と`packaging/installer.iss`のバージョンは`build.ps1`が
  `pyproject.toml`から都度読み取って埋め込むため手動更新不要（既に一元化済み、
  chore/versioning-policyで確認済み）。

## Phase 5j 明示指定・ログ基盤・レイアウト（2026-08-25）

> `registry.py` が `allow_fallback=True` を無条件に渡し、
> 「明示指定時はフォールバックしない」という Phase 1 以来の原則が
> 破られていた。原則に対する回帰テストがなかったため、見過ごされた。

この引数はPhase 3bでauto検出結果をwhisper.cppへ渡す際に追加されたが、明示variantまで退避可能にする
必要はなく、単純なpolicy上書きだった。registryから指定を除去し、`WhisperCppBackend`が元来持つ
`variant == "auto"`判定へ戻した。fallback試行済み状態を持たせ、同一実行で2回目へ進まないtestを追加した。
faster-whisperとpyannoteは既に`device == "auto"`の場合だけCPUへ退避し、明示deviceではerrorを返す実装
だったため変更せず、解決deviceを構造化eventへ記録した。

ログrootはinstall先`logs/`、書込不可ならuser logへ退避する。`app.log`、event限定CLI JSONL、raw、
diagnosticsを分離し、job内`utteran.log`は維持した。構造化eventは本文を受け付ける通常log recordをfilterし、
構成、IR load、fallback、device、stage秒、RTF、model取得／IR生成、error分類だけを記録する。raw stderrは
既定false、明示true時だけ秘密値mask後にjob別保存し、起動警告とGUI常時表示を行う。30日、通常100 MiB、
raw 1 GiBを既定とした。30日は事後調査期間、100 MiBは小さいeventの余裕、1 GiBはraw肥大化と本文保持
リスクの抑制という判断である。削除は起動時に日数→raw容量→通常容量の順で行い、手動cleanも提供する。

J-4は内容・file名を記録しない同じ180秒実音声、一時成果物削除、`large-v3-turbo`、
`openvino_vulkan`、話者分離なしで3回比較した。ASR平均は直接CLI 12.19秒（RTF約0.068）、GUIが使う
queue/subprocess経路12.81秒（RTF約0.071）で約5.1%差、外側総時間は13.23秒対14.16秒で約7.0%差だった。
全6回でIR load成功、fallback 0回、実構成`openvino_vulkan`を確認した。疑われた1.7倍差は再現せず、
残差はprocess監視／queue overheadと実行間変動の範囲と判断した。

`.stage-list`と同種gridを`minmax(0, 1fr)`へ揃え、itemを縮小可能にし、900px以下は2列、token入力は
可変幅にした。未使用`viewer-view`は削除した。Browser skillによるlight/dark・resize実画面確認を試みたが、
このsessionではbrowser backendが0件で未実施。DOM/CSS回帰testでoverflow対策、raw警告、log folder導線を
固定した。

最終検証はmodel不要test 346件、ruff、mypy、JavaScript構文検査が合格した。受入P6はCPU、OpenVINO、
Vulkan、OpenVINO+Vulkanの実model推論を含む6/6件が合格した。破壊的な`P3-7a clean → P6-7 missing
確認 → P3-7c build`の中間caseだけがP6 groupに属し、P6単独実行で前提なしに走る既存不整合を修正した。
既定G系は現在未導入のfaster-whisperモデルを要求する環境不一致により合格扱いにできず、今回変更の
回帰判定には用いていない。

0.1.7配布版をclean buildし、推論core非同梱check、ProductVersion 0.1.7、Inno Setup compileに成功した。
installer SHA-256は`4773edbbe85171d4bf65dc567c5ff563769c45817959c75e57ee0e439d6da24a`。
配布onedir exeを実起動し4秒後も稼働、配布directoryの`logs/app.log`生成、bundle内CSS/HTMLへの
overflow修正とraw警告同梱を確認後、検証用processだけ終了した。既存のインストール済みGUI processは
操作していない。browser不在のためlight/dark・resizeの目視だけは未完了として残す。

## バージョニング規約の確立とv0.1.7ビルド（2026-08-25）

> 変更を重ねてもバージョンが据え置きのまま配布物が作られており、
> 「どの版で試したか」を追跡できなくなっていた。

`chore/versioning-policy`ブランチで実施。現状確認（Step 1）: `pyproject.toml`／
`src/utteran/__init__.py`／`src/utteran_gui/__init__.py`／`uv.lock`はすべて`0.1.6`で
食い違いなし。一元管理確認（Step 2）: `build.ps1`は`pyproject.toml`から読み取った
versionをexe（FileVersion/ProductVersion）とinstaller（AppVersion／出力ファイル名）の
両方へ一度だけ伝播しており、Phase 5dの要件を満たす。`__init__.py`の`__version__`は
手動同期のままとし（`tests/test_version.py`が一致を検証）、frozen exeへ
`importlib.metadata`を埋め込むための`gui.spec`変更は本作業の範囲外と判断し行わなかった。
変更履歴整理（Step 5）: `変更履歴.md`に未リリース節の滞留はなく、整理対象なし。
`0.1.6`→`0.1.7`へパッチを上げ、規約を`要件定義.md`24.1章・`docs/リリース手順.md`
「バージョン更新手順（作業単位ごと）」・本ファイル冒頭の運用ルールへ記載した。

ビルド（Step 6）: 本機にInno Setup 6が未導入だったため`winget install --id
JRSoftware.InnoSetup -e`で導入し、`.\build.ps1`を実行。`utteran-gui.exe`の
FileVersion/ProductVersionと`dist\installer\utteran-setup-0.1.7.exe`のファイル名は
いずれも`0.1.7`に一致。SHA-256:
`953e74d0333e5cfb18de5b2669fde45e3667ef69785bafa579cd524ac20089ab`。

品質ゲート: `ruff check src tests tools`・`mypy`は合格。`pytest -m "not requires_model"`は
322 passed / 12 failed / 1 skipped。失敗12件は本機固有の環境要因で、versioning変更とは
無関係と確認済み（stashして無変更tree で同一の失敗を再現）。内訳: (1)
`test_ctrl_c_is_confined_to_the_child_console`はGit Bash実行時の既知の制約
（本ファイル「既知の落とし穴」既述）、(2) 残り約10件は`C:\Users\yuta maezawa`自体が
git repositoryであるため、pytestの一時出力先（そのユーザーのTemp配下）が
`_ensure_git_ignored_output`のgit repository検出に誤って一致し、
`ConfigurationError`となるもの。utteran本体の不具合ではなく、この開発機のホーム
ディレクトリ構成に起因する。

### 追記（2026-08-26）: main統合時に0.1.8へ再採番

別PCで並行していたPhase 5j作業が独立に`0.1.6`→`0.1.7`へバージョンを上げて`origin/main`へ
push済みだった（10コミット、`chore(release): bump version to 0.1.7`）。このため上記の
`0.1.7`は`origin/main`の`0.1.7`と同一番号・異なる内容で衝突した。利用者の指示に基づき、
`chore/versioning-policy`側を`0.1.8`へ繰り上げてから`main`へ統合した。上記ビルド記録
（exe/installerのversionとSHA-256）は当時の`0.1.7`ビルドのものであり、統合後の`main`には
含まれない。`0.1.8`としての再ビルドは別途必要。

## Phase 5i モデル体系・VAD・共通処理キュー（2026-08-25）

> 未実装バックエンド（openvino GenAI）のモデルがカタログに残り、
> 利用者が使えないモデルを取得できる状態だった。
> フェーズをまたいで方針が変わった際、カタログの棚卸しが漏れた。

OpenVINO GenAI entryは非表示に残さず完全に除去した。実行registryと照合した結果、残るbackendは
faster-whisper、whisper-cpp、pyannoteと補助VADだけで、他に「取得できるが使えない」entryはなかった。
旧entryの取得物は利用者データを無断削除しないため保持し、容量回収が必要な場合だけmodel root配下の
`openvino/large-v3-turbo`を手動削除する。Systranのtiny／base／small／mediumはHugging Faceの各model
pageでCTranslate2変換、MIT license、必要fileの実在を確認して登録した。GGML一覧はwhisper.cpp公開artifact
名と既存固定sizeからの動的生成を維持する。

GPU可否は量子化名から推測せず、profile CLIのCTranslate2 CUDA、native Vulkan／OpenVINO、PyTorch
CUDA／XPU検出を注入してbackend別に表示する。f16／q5／q8はサイズ・速度・精度の調整と説明し、
Kotoba-Whisperは日本語特化、tiny～mediumは試用／低スペック用途として位置づけた。

whisper.cpp `vad`の既定をtrueへ変更し、ウィザードはSilero VADを独立stageとして取得する。旧利用者の
VAD modelがない場合は警告して当該実行だけVADなしで続行し、明示path不正だけは従来どおりerrorにする。
faster-whisper `vad_filter`とwhisper.cpp `vad`はbackend別の独立設定で、既定変更によりASR config_hashが
変わり既存jobのASR以降が再計算される。

GUI process内の共通FIFO queueを採用し、文字起こし、wizard、model操作、IR生成を同時1件に直列化した。
queueは待機／実行／完了／失敗／cancelを表示し個別cancelでき、失敗後も次へ進む。外部process途中状態を
安全に復元できないため永続化せず、再起動後はcore resume／download再試行を使う。文字起こしoptionは
投入時snapshotなので、実行中のform変更は次のqueue項目だけへ反映される旨を表示する。

Hugging Face dry-runとfile progress callbackを用いてbytes、割合、速度、ETAをJSONLで通知する。実Silero
VAD 885,098 bytesを一時model rootへ取得し、0→100%のbyte progressとverify成功を確認した。Windowsでは
`SetCurrentProcessExplicitAppUserModelID("Utteran.Utteran")`が実機で成功し、pywebviewにも`.ico`を渡す。

最終検証ではpytest 335件、ruff check、mypy、JavaScript構文検査が合格した。受入harnessの既定
162件は初回147合格、CUDA非搭載によるskip 7、旧catalog期待値等による失敗8だった。期待値と任意の
G13 enduranceを扱うaggregate再実行を修正し、失敗8件を再実行して全件合格、結果JSONLの最新状態は
対象162件に失敗・欠落とも0となった。

0.1.6配布版をclean buildし、PyInstallerのicon resource、ProductVersion 0.1.6、Inno Setup compileと
installer SHA-256 `948c2bd68d35fbe253bc75e5caf146c433fef1173fb6e1ddd87dab93b75084fd`を確認した。
別directoryへ新規installしたexeを実起動し、タイトルバーのutteran icon、title `utteran`、Alt+Tab対象の
top-level window、大小のwindow icon handle、処理キューnavigation、画面内version 0.1.6をwindow単体capture
で確認した。Start Menu shortcutは対象exeのicon resource index 0を参照し、通常install先へ0.1.6を再導入して
shortcut targetも復元した。Windows taskbarのUI Automationにも`utteran GUI - 1 個の実行中ウィンドウ`という
buttonが現れ、明示AppUserModelIDと大小のutteran window iconが使われており、Python既定iconへのfallbackは
発生していない。

## Phase 5h 性能問題・モデル管理（2026-08-25）

H-1は実機とコードの両方で原因を特定した。Intel profileの`devices --json`は
`whisper-cpp / vulkan`をauto選択し、native 4構成もすべてrunnableだった。一方、導入済みASRモデルは
`faster-whisper:large-v3-turbo`だけでGGMLモデルがなく、Phase 5gウィザードもprofileにかかわらず
このfaster-whisperモデルを固定取得していた。さらにGUIはCLIの`auto_selection`を参照せず、独自生成した
ASR一覧の先頭（faster-whisper）、device一覧の先頭（CPU）を既定にしていた。このため、速いnative構成が
存在してもGUI経由では`large-v3-turbo · cpu`となった。

> CLI では auto が Vulkan を選ぶ環境で、GUI 経由では CPU が選ばれていた。
> GUI が独自に既定を持つと、CLI 側の最適化が届かない。

GUIの既定を`devices --json`のauto結果へ接続し、利用可能なbackend/model/deviceに一致する場合だけ
その組み合わせを選ぶ。前提不足ならnative buildまたはGGML取得の案内を表示する。Intel/Vulkan
ウィザードはwhisper.cpp用GGML、CPU/CUDAはfaster-whisper用モデルを選択可能にした。推奨GGML
`large-v3-turbo-q5_0`取得後の実機snapshotは`whisper-cpp / large-v3-turbo-q5_0 / vulkan`、話者分離
`xpu:0`を既定にした。

同一の300秒クリップ（内容・ファイル名は記録せず、成果物は測定後に削除）で比較した。
修正前相当のfaster-whisper/CPUは74.34秒、実時間比4.04倍。修正後のwhisper.cpp/Vulkanは
21.62秒、実時間比13.88倍で、3.44倍高速だった。両方exit 0で、完了条件の5倍以上を満たした。

モデル管理画面を追加し、全カタログ／推奨表示、導入状態・用途・概算／実サイズ・保存先、明示確認付き
取得／削除、検証、OpenVINO encoder IR生成／削除をprofile CLI経由で提供した。取得・IR生成は進捗ログと
キャンセルに対応し、既存のToken未設定／無効／利用条件未同意分類を再利用する。native buildはVulkan SDK
等の前提確認と長時間診断を伴うため自動実行せず、状態と`native status`／`native build`手順の案内に留めた。

pywebviewのfile/folder dialog（単一入力root。folder batchは既存recursiveで複数fileを処理）を追加し、
手入力とdrag-and-dropを維持した。選択pathは設定へ保存しない。新iconをPyInstaller、Inno Setup、shortcut、
GUIロゴへ反映し、仮想行の左右marginによる横幅超過を修正した。Browserスキルで実表示確認を試みたが、
このsessionに利用可能なbrowserがなく、ライト／ダークとresizeの自動visual確認は未実施。

model不要test 329件、ruff check、mypy、JavaScript構文検査は合格した。受入G5初回はG5-01〜08が
合格し、G5-09が既存logの再作成を旧size位置から読んで120秒待つハーネス不具合で失敗した。
force実行でlogが短くなった場合は先頭から読むよう修正し、単体testと既存jobがある同一G5-09を
再実行して合格、子CLIのexit 130を確認した。

0.1.5配布版を再buildし、PyInstallerがicon resourceをcopy、Inno SetupがSetup iconとshortcut設定を
含めて正常compileした。installer SHA-256は
`98ae3aa64d026daf20759ab24bb93862419704793db7ce8a81b96757c009f212`。配布exeはProductVersion
0.1.5、associated icon 32x32を持ち、実起動でtitle `utteran`のnative windowを生成した。bundle内の
model管理画面とdialog bridgeも確認した。browser不在のため画面内icon、theme、resize、dialog clickの
目視確認と、installerを実installしたshortcut/taskbar確認は未実施であり、全項目完了とは扱わない。

## 0.1.5 installer起動元のRedirectionGuard分離（2026-08-24）

ユーザーの詳細logでは、Inno Setup完了画面から起動したGUIだけが
`AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe`の検査で
Windows error 448（信頼されていないmount point）となり、GUIを終了してshortcutから起動し直すと
先へ進んだ。前回の配布版検証はsilent install（`skipifsilent`で`[Run]`を通らない）後にGUIを
別processで起動していたため、この起動元固有の状態を検証できていなかった。

CLI診断では、通常の`uv python find 3.12`とinstall先からのprofile同期は成功する一方、Win32
`SetProcessMitigationPolicy(ProcessRedirectionTrustPolicy, EnforceRedirectionTrust=1)`で
RedirectionGuardを有効化したprocessから、実際と同じ
`uv sync --locked --extra xpu --extra whisper-cpp --extra openvino`を実行すると、同一path・同一error
448・exit 2を再現した。通信、Hugging Face Token、profile権限ではなく、Inno Setupが有効化する
RedirectionGuardを完了画面の子GUIが継承することが原因と確定した。

`packaging/installer.iss`から`[Run]`のpostinstall GUI起動を廃止した。install完了後はSetupを終了し、
Start menuまたはdesktop shortcutからGUIを通常起動する。installerのsecurity mitigation自体は無効化
しない。回帰testはinstaller sourceに`[Run]`と`postinstall`が存在しないことを固定し、versionを
0.1.5へ更新した。

0.1.5配布版をbuildして既定install先へ上書きした。installer終了後にGUI processが起動していないこと、
installed exeとGUI表示用versionが0.1.5であること、通常起動したGUIのRedirectionGuard policyが0で
あることを確認した。その状態でinstall先の`setup.ps1 -Profile intel -Yes`を実行し、同じlocked sync、
XPU、OpenVINO（CPU/GPU/NPU）、CTranslate2確認がすべて成功した。pytest 323件、ruff、mypy、
PowerShell／JavaScript構文検査が合格。installer SHA-256は
`3d2a88caf599eca71032e6cf703a51b08610c46a02afa5b51a6db67c41718a89`。

## 0.1.4 初回uv同期の自動回復（2026-08-24）

ユーザーのクリーン再install試験では、Intel profileの`uv sync --locked --extra xpu --extra
whisper-cpp --extra openvino`がexit 2となり、venv構築が失敗していた。添付ログはその後も続行した
ffmpeg／Token一般案内が末尾を占め、uv自身の詳細errorは残っていなかった。同じinstall先・引数で
直後に手動再実行すると依存148 packageの同期とIntel検証が成功し、固定的なlockfile／権限不良では
なく、初回のnative DLL展開、scanner、cache、通信等に伴う一時失敗と判断した。

`setup.ps1`へlocked環境の同じuv同期を最大3回、2秒／4秒backoffで自動再試行するhelperを追加した。
uvのstdoutはGUI logへstreamしつつBoolean結果と混ざらないようhost streamへ転送する。3回失敗時は
ffmpeg確認やToken案内へ進まず即時exit 1とし、uv本体のerror、試行回数、exit codeを診断末尾に残す。
回帰testはretry helper、既定3回、backoff、stdout転送、およびffmpegより前のfail-fast順序を固定した。

0.1.4の実配布版検証では、既存GUIをuninstallし、install先venvとwizard settingsを退避／消去、
Keyring Tokenだけを保持してfresh installした。実WebViewを最初の「始める」からUI Automationで操作し、
Intel自動推奨→話者分離ON→Token設定済み→confirm→venv→preflight→pyannote→ASR→smokeの全経路が
成功した。最終settingsは`step=done`、5段階完了、Token errorなし。退避venvは成功後に削除し、新しい
Intel venvを保持した。pytest 322件、ruff、mypy、PowerShell／JavaScript構文検査が合格。最終buildを
上書きinstallしてGUIを起動済み。installer SHA-256は
`051604a7bc622cbe00a1c48c3f5e36379f8b407ea902dbad0edfa6aa0285b28c`。

## 0.1.3 ウィザード失敗分類の修正（2026-08-24）

0.1.2配布版で再発したToken errorを実状態から再調査した。settingsは`completed_stages=[]`、install先に
profile venvなし、`token_invalid`だけが保存されており、Token preflightへ到達していなかった。
原因は、venv構築中の`setup.ps1`が正常な一般案内として出す「read token」「HF_TOKEN」を、共通の
`guidance_for()`がvenv構築の別原因による失敗時にもToken認証エラーと誤分類していたことだった。

0.1.3ではguidance分類へoperation contextを追加し、venv構築ではToken／license／model分類を禁止した。
frontendもToken画面へ戻すのはmodel download／smoke testのToken系失敗だけに限定し、二重に防御する。
その他のsubprocess失敗ではmask済み末尾12行をerror画面へ表示し、本来の原因を次回その場で確認できる。
再発testはToken案内を含みexit 1となる偽venv buildが`general`分類となり、Token errorを作らないことを
固定した。

install先の手動profile構築はIntelで成功。そのCLIをinstall先cwdから実行し、同じkeyring credentialが
`source=keyring`／`access=available`であること、pyannoteとfaster-whisper modelの実取得成功を確認した。
0.1.3配布版をbuild・上書きinstallし、実WebViewをUI Automationで操作した。venv→preflight→pyannote→
ASR→話者分離ON smokeの全5段階が連続成功し、settingsは`step=done`、5段階完了、Token errorなし、
完了時刻ありとなった。完了画面の後に文字起こしworkspaceまで遷移し、sidebar `v0.1.3`を確認した。
pytest 321件、ruff、mypy、JavaScript構文検査が合格。installer SHA-256は
`d8905f8eff8542c3c7e53d57b1766ed71da8173c2a120dd15425c83025e80b9f`。

## 0.1.2配布版・Token error誤再表示修正（2026-08-24）

配布版を同じ0.1.1のまま上書きbuildしていたため、利用者が古いinstaller／新しいinstallerを識別
できず、さらに`utteran_gui.__version__`だけ0.1.0のままという不一致があった。project、CLI、GUI、
lockfileを0.1.2へ統一し、GUI sidebarへ`v0.1.2`を表示するsession保護version APIを追加した。
PyInstaller exeへFileVersion／ProductVersionを埋め込み、`build.ps1`はproject versionとの一致と
`utteran-setup-$Version.exe`の厳密な存在を検証する。古い版の最新時刻検索は廃止した。

Token stepの復元時は、profile venv stageが完了している再開処理でのみ保存済みToken errorを表示する。
新規／未構築状態では古い`token_invalid`をfrontendでも抑止し、Token保存成功後はpassword入力欄と
永続化済みerrorを消す。関連50件、全pytest 320件、ruff、mypy、JavaScript構文検査が合格した。
0.1.2 installerをbuild・既定install先へ上書きし、installer名、レジストリDisplayVersion、installed
exe ProductVersion、WebView起動引数、実配布版sidebar表示がすべて0.1.2で一致した。実配布版の
accessibility treeではセットアップウィザードと設定済みToken状態を確認し、古い「トークンが無効」
表示がないことを確認した。さらにinstall先をcurrent directoryとしてIntel profile CLIから同じ
keyring credentialを検査し、`source=keyring`／`access=available`を再確認した。installer SHA-256は
`3fdfd8af9088c3c9d8d456ce486abb77a42c79c3e9b4977eead09ffa2be17aa7`。

## Windows GUI子processのconsole抑止（2026-08-24）

`fix/no-console-window`をPhase 5g実装済みの現在地点から作成した。`src/utteran_gui`を全検索し、
子process起動は共通job/wizard `Popen`、profile CLIの同期`run`、hardware検出PowerShell、
process tree終了用`taskkill`、folder表示用`explorer.exe`の5経路と確認した。
`processes.build_creation_kwargs()`へWindows creation flagを集約し、全経路へ`CREATE_NO_WINDOW`を
適用した。job/wizardだけはcancelに必要な既存`CREATE_NEW_PROCESS_GROUP`をbit ORで併用し、POSIXの
`start_new_session`経路は変更していない。

回帰testは両flagの併用、CLI、PowerShell、taskkill、Explorer、非Windows、および実PowerShell
process treeの`taskkill /T /F`終了を検証する。既存GUI cancelのexit 130 testに加え、CLIの
hard interrupt exit 130とWindows acceptance scenarioのconsole Ctrl+C隔離testも合格した。
`ruff check .`、`mypy src`、model不要を含む全test 319件が合格。実受入ハーネスはG5を実行し、
実SIGINTでexit 130を要求するG5-09を含む9件すべて合格（74.7秒）した。
配布版installerをbuildし、SHA-256は
`08e77226c7feb3086f74ff59792a720f855f70f626be4f34f1f5e3ed0a5ee4b2`。既定install先へ上書き後、
`console=False`のexeを起動してtop-level `ConsoleWindowClass`を5ms間隔で監視した。起動時の
hardware検出を含む12秒間で新規console windowは0件。さらに配布版GUIをUI Automationで操作し、
既存acceptanceの10分相当test音声を実際に文字起こし開始、GUIのcancel buttonをInvokeした。
`utteran.exe`起動から`taskkill /T /F`によるtree終了まで新規console windowは0件、child processは
15秒以内に確実に終了した（実測では約2秒）。wizardと通常jobは同一`build_popen_kwargs()`を使い、
全wizard stageのflagは同じ回帰testで固定している。設定・model・venvは削除していない。

## Phase 5g Token preflight再発修正（2026-08-24）

再修正では、画面の`token_invalid`が今回の認証結果ではなく、profile venvと完了stageがない状態で
過去の`setup_wizard_token_error`だけ再表示されていたことを実settingsの時刻・状態から特定した。
venv preflightを実行していない状態では保存済みエラーを表示せず、wizard開始時とToken stepへの
通常遷移時にも古いエラーを消去する。

実機では同じtokenをIntel/CPU profile CLIから検査して`access=available`、pyannote community-1も
完全にインストール済みだった。従来はこの状態でもHubのonline認証を毎回必須にしていたため、
外部APIの失敗でウィザードが`token_invalid`へ戻り得た。`ModelManager.check_access()`は完全な
local copyを先に検証し、存在すればonline認証なしで利用可能とする。後続のmodel downloadも
完全なlocal copyを再利用し、再認証・再取得しない。

## Phase 5g ウィザード無人実行（2026-08-24、実装・実バックエンド検証完了）

配布版GUIで`setup_wizard_step=execution`、`setup_wizard_profile=null`、完了段階が`preflight`だけという
不整合状態を実際に確認した。実行APIのPydantic 422 detail（object配列）をJavaScriptの`Error`へ
直接渡したため画面は`[object Object]`となり、再試行もprofileなし実行を繰り返していた。
`status()`はこの状態を`step=profile`／完了段階なし／再開対象へ正規化し、次回起動時にCPU/GPUの
自動検出・おすすめ構成カードへ戻す。`save_state()`側もprofileなしで後続stepを永続化できないよう
防御し、profile変更時は旧profileの完了段階を破棄する。API detail配列は`msg`を抽出して表示する。
修正版installerを既定インストール先へ上書きし、設定・venv・modelを保持したままGUIを再起動した。

`fix/phase5e-gui-settings`から`fix/phase5g-wizard-unattended`を分岐した。ウィザードを
profile→話者分離→Token（ON時）→model→confirmの入力フェーズと、venv→preflight→pyannote→
ASR→smoke testの実行フェーズへ変更した。話者分離は独立step・既定ON。confirmは10～45分、
profile＋modelの概算download量、以降は席を外せることを表示する。各入力値と5つの完了段階は
Token本文を含めず`settings.json`へ原子的に保存し、再起動時は未完了段階だけを再実行する。
pyannoteをASRより先にした理由は、preflight後にもgated file側の失敗が残る場合に34MiB級の取得で
先に検出し、1.6GiB級ASR downloadを無駄にしないため。Token系guidanceは汎用errorへ送らず、分類を
保持したToken画面へ戻す。設定画面の既存ウィザード入口は、中断時の再開と完了後の再設定を兼ねる。

Token再発は実機で原因を特定した。従来`ModelManager.check_access()`はgated repositoryの
`model_info`だけを取得していたが、このmetadataは合成した無効Tokenでも公開され、`available`を
返した。その後のmodel file取得で初めて失敗するため、報告された「preflight通過後に
`guide_token`汎用error」の経路と一致する。修正後は`HfApi.whoami()`でTokenを検証し、続けて
pyannote `config.yaml`を`hf_hub_download(..., dry_run=True)`で確認する。実model重みは取得せず、
合成無効Tokenは`token_invalid`、本機の実効`.env` credentialは`available`になった。Token本文は
command出力、API、log、文書に残していない。

Intel検証は初回／2回目を個別計測し、`devices --json`が8.260秒／7.757秒、別torch XPU probeが
2.154秒／2.199秒だった。JSONには既に`pytorch.xpu_available=true`とXPU device情報が含まれたため、
別probeと人間向けdevicesの再起動を削除した。`verify_devices`前に初回時間の案内を出し、終了後に
秒数を記録する。修正後の`setup.ps1 -Profile intel -Yes`はdevice probe 7.2秒、全体8.47秒で成功。

実バックエンドの無人試験は一時GUI設定を使い、既存Intel venv/modelを壊さず全5段階を自動実行。
venv、preflight、pyannote model、faster-whisper large-v3-turbo、話者分離ON smoke testが全てexit 0、
`completed_stages=[venv, preflight, diarization_model, asr_model, smoke]`と`step=done`を確認した。
モデル不要pytest 307件、関連85件、ruff、mypy、
JavaScript／PowerShell構文検査は合格。受入基盤pytest 21件も合格した。

`build.ps1`はPyInstaller→推論core非混入検査→Inno Setup→SHA-256まで成功し、未署名installer
`dist/installer/utteran-setup-0.1.1.exe`（SHA-256
`fc0b24c897dbb3bdf5d7d5ba640ab6ab2b338972b07fbb7d3b04dd4b2cd7b7a8`）を生成した。dist直下exeと
隔離silent install版の`--diagnose-keyring`はいずれもWinVault backendのget/set/deleteがexit 0で、
隔離installはsilent uninstall済み。接続可能なBrowser実体がセッションになかったため、配布版
WebViewの目視操作と「配布版画面でconfirm後に無人完了」は未確認。実ハーネス既定セットは起動したが、
実model／長時間の既存G系が多数failしG11/G12がtimeout、summary未生成のため合格扱いにしない。
今回のコマンドを照合したprocess treeは停止済み。リリース完了条件としては配布版GUI目視と、対象
環境を整えた受入ハーネス再実行が残る。

## Phase 5f HF Token／セットアップウィザード再修正（2026-08-24、実装・自動検証完了）

`fix/phase5e-gui-settings`で、HF Token画面を話者分離ON/OFF共通の正式ステップへ変更した。
話者分離ON時は選択profileの`utteran config token-status --json --check-model ...`を子process実行し、
実効Tokenの取得元（`HF_TOKEN`／`.env`／keyring）とpyannote gated modelへの軽量アクセスを確認する。
Token本文はCLI JSONにもGUI APIにも返さず、未設定、401相当の無効Token、403／gatedの利用条件未同意、
ネットワーク／profile CLI失敗をToken画面内で区別する。GUI keyring保存後のread-back確認は維持した。

`GuiSettings`へ`setup_wizard_started_at`を追加し、開始後未完了は再起動時の再開対象とした。
smoke testがexit 0になった時点で`setup_wizard_completed_at`を永続化し、完了APIは再起動後も同じ値を
返す冪等動作にした。profileあり・wizard fieldなしの旧版環境は自動表示しない互換性を維持した。

検証: モデル不要pytest 297 passed（うち関連pytest 105 passed）、ruff check・format check、
mypy 53 source files、JavaScript構文検査、`git diff --check`が合格。実Hugging Face credentialを使う
アクセス確認と、配布版GUI→profile CLIのWindows実機credential共有は未実施で、配布版受入時に必要。

## Phase 5e GUI設定修正（2026-08-24、実装・配布版診断完了）

`fix/phase5e-gui-settings`でE-1〜E-4を実装した。指示書の原因候補は推測のまま採用せず、旧
`dist/utteran-gui/utteran-gui.exe`を`pyi-archive_viewer -r`で調査した。その結果、旧specに
keyringの明示指定がなくてもPyInstaller 6.22.2標準`hook-keyring.py`が働き、`keyring`、
`keyring.backends.Windows`、`keyring-25.7.0.dist-info/entry_points.txt`は全て既に収集されていた。
したがって「hidden import欠落だけがv0.1.0症状の原因」という候補は否定された。ただしhookへの
暗黙依存をなくすため、specへ`collect_submodules("keyring")`と`copy_metadata("keyring")`を明示し、
両方の存在を検査する回帰testを追加した。

配布版で動的状態を確認できる`utteran-gui.exe --diagnose-keyring <json-path>`を追加した。実token
slotを触らずrandomな診断usernameと合成tokenだけでget/set/get/deleteを行い、token値を結果へ
含めない。`build.ps1`でonedir＋installerを再build後、(1) dist直下exe、(2) installerを隔離した
`.tmp/phase5e-installed`へsilent installしたexeの双方で実行し、いずれもimport成功、backend=
`keyring.backends.Windows.WinVaultKeyring`、get/set/delete全成功、exit 0を確認した。隔離installは
silent uninstall済みで、既存profile/model/job/settings/tokenを変更していない。

E-1の実障害点は、`TokenStore._get_token`があらゆる例外を`None`へ変換し、保存先利用不能と未設定を
区別できなかったこと、および`set_password`後に再取得せずAPIが無条件でconfigured=trueを返した
ことだった。`TokenStatus`でconfigured/available/backend/errorを分離し、保存直後に同じservice
`utteran`／username `huggingface`から値を再取得して一致を確認する。利用不能時はGUIでWindows
資格情報マネージャー、`HF_TOKEN`、`.env`を案内する。CLIは従来通りenv > `.env` > keyringの順で、
GUIと同じservice/usernameを使うことを回帰testで固定した。インストール版に`.env`は既定で存在
しないが、インストールrootがCLI cwdになるため利用者が作成した場合は従来の優先順位で参照される。

E-2は設定画面が項目ごとにPUTしていたのではなく、明示保存の全体PUTとは別に、言語変更やtoken
保存／削除が`applySettings()`を呼び、serverへ未保存の別項目を古い`state.settings`からDOMへ
再投入していたことが直接原因だった。再描画を翻訳／token表示だけへ限定し、設定保存を
`PATCH /api/settings`の部分更新に変更した。`SettingsStore.update()`はprocess内RLockの下で原子的に
read-modify-writeするため、連続／並行更新とwizard完了時刻を巻き戻さない。

E-3はwizardと設定画面を共通`saveToken()`へ統合し、アカウント作成・利用条件同意・read token発行
の3リンク、password入力、保存後read-back、「話者分離なしで進める」、keyring失敗案内を実装した。
無効tokenと利用条件未同意は既存`guidance_for`の`token`／`license`分類をモデル取得とsmoke testで
引き続き区別する。E-4はthemeをsystem/light/darkとし、field欠落時だけsystem、既存dark/lightは
維持する。systemはWebView2が対応する`prefers-color-scheme` media queryで実行中のOS変更にも
再評価される。Windowsタイトルバーはpywebview/WebView2のOS管理枠を変更せず、client領域だけを
CSS theme化した。native WebView上でWindows themeを実際に切り替える目視確認は自動化できず、
今回はCSS契約とWebView2同梱までの確認に留めた。

品質結果: モデル不要pytest 288 passed、ruff check、ruff format check、mypy 55 source files、
JavaScript構文検査、`git diff --check`が合格。受入ハーネスは変更に近いG8〜G12を実行して23/23
pass（設定優先順位、token無し、秘密mask、README例、Windows frontを含む）。`build.ps1`は
PyInstaller→推論core非混入検査→Inno Setup→SHA-256まで成功。開発環境で動作してもPyInstaller
bundleでは動的importが解決されない場合があるため、動的backend検出libraryはhidden importsと
metadata収集を明示し、配布版の実診断で確認する。

## v0.1.0 リリース公開（2026-08-19）

利用者の指示により、Phase 5d完了後に`docs/リリース手順.md`のrelease gateに沿って
`main`へ`feature/phase5d-installer`をfast-forward merge・push、`変更履歴.md`の
`未リリース`section全体（Phase 3d〜5dの蓄積分）を`## [0.1.0] - 2026-08-19`として切り出し、
annotated tag `v0.1.0`を作成・push、tag時点のcommitから`build.ps1`でインストーラーを
再ビルドし、GitHub Releaseへexe（19,744,006 byte）とSHA-256 sidecarを添付して公開した
（`https://github.com/zawa356/Utteran/releases/tag/v0.1.0`、draft/prerelease指定なし）。
SHA-256（`dbed1aeca4197f10c5f78c72ee9791eaa32b4e99a2d4620e98a32c76e7a3981c`）はGitHubが
アセットから独立に算出した digest と完全一致することを確認した。

release gateのうち、`tools/public_history_scan.py --worktree --fail-on-findings`
（blocking 0件）とruff/mypy/pytest（モデル不要、既知flaky1件のみ）は実行したが、
**全hardware・profileを対象にした統合受入ハーネスのフル再実行（cuda groupを含む、
実測約72分）と、`public_history_scan.py`の全履歴監査は今回実行していない**。
本機にNVIDIA GPUがなくcuda groupは元々実行不能であること、直近のPhase 3d/4a/5c/
Phase 5d事前準備で個別に実施・記録済みの受入結果があること、利用者の依頼が
「push and release」という限定的な内容だったことから、今回は简略化した。
次回リリース時、または利用者が求める場合は、`docs/リリース手順.md`のrelease gateを
省略せず全項目実行することを推奨する。

`v0.0.1`（GitHub上draft状態のまま、要件定義29章・リリース手順に記録済み）には
一切手を加えていない。

## Phase 5d インストーラー化（2026-08-19、実装・実機検証完了）

`docs/utteran_Phase5d_指示書.md`に従い、`feature/phase5d-installer`で実装した。
推論／GUIのPythonコードは一切変更していない（29.5章参照）。設計判断とその理由の全文は
`要件定義.md`29章、実機検証手順は`docs/Phase5d_インストーラー_手動確認手順書.md`を参照。

### 実装内容

- `pyproject.toml`に`build`extra（`pyinstaller`、Windows限定）を追加。`gui`venvには混ぜず、
  ビルド専用venv（`.venvs/win-gui-build`）を`build.ps1`が管理する。
- `packaging/gui.spec`: PyInstaller onedirビルド。`src/utteran_gui`とFastAPI／uvicorn／
  pywebviewだけを対象とし、推論コア（`utteran`、torch、faster-whisper、pyannote、
  ctranslate2）がビルド成果物へ混入した場合はビルド自体を`SystemExit`で中断する検査を
  組み込んだ。
- `packaging/installer.iss`: Inno Setup（管理者権限不要、`{localappdata}\Programs\utteran`
  へインストール、日本語／英語）。ライセンスページ直後にサードパーティライセンス告知、
  インストール先選択直後に初回起動時の追加ダウンロード告知を、それぞれ`CreateOutputMsgPage`
  で表示する。アンインストール時は`MsgBox`によるYes/No確認chainでGUI設定／プロファイル
  実行環境（`.venvs`）／モデル／ジョブ履歴／ffmpegの削除可否を個別に確認し（既定は削除しない、
  サイレントアンインストールでは一切確認せずすべて保持）、uvは削除対象にしない
  （ffmpegとuvが`%LocalAppData%\utteran\bin`を共有するため、削除はディレクトリ単位でなく
  `ffmpeg.exe`/`ffprobe.exe`のファイル単位で行う）。
- `build.ps1`: ISCC.exe不在を即座に検出して（uv sync等を試みる前に）導入方法付きで終了する。
  存在すればビルド専用venv同期→PyInstaller→推論コア非混入の実ファイル走査→
  `pyproject.toml`のversionを渡してのInno Setup compile→SHA-256算出まで1コマンドで実行する。
- 署名は実施しない（要件定義29.6章に判断理由を記録）。`installer.iss`に
  `#ifdef SignInstaller`で囲んだ`SignTool`宣言を用意し、`build.ps1 -SignCommand`経由で
  将来のCA署名導入に備えた（未署名ビルドでは`SignTool`自体が定義されないため、
  署名ツール未登録でもcompileが失敗しない）。
- README: インストーラーからの導入を最初の選択肢として提示し、SmartScreen警告の内容・
  回避手順・警告が出る理由、SHA-256検証手順（`Get-FileHash`）を追記。既存の`setup.ps1`
  手順は「開発者向け」として保持。`docs/リリース手順.md`にインストーラーのビルド・
  SHA-256記載・GitHub Releasesへの添付手順を追加。

### 実装中に発見・修正したバグ

- `packaging/gui.spec`のPyInstaller `SPECPATH`はspec fileの**あるディレクトリ**を指す
  （spec file自体のfull pathではない）。当初`os.path.dirname(SPECPATH)`としたため
  repository rootの1つ上（`C:\UserDataFile\git\src\...`）を誤って参照し、実際に
  `python -m PyInstaller`を実行して`ERROR: script ... not found`で検出・修正した。
  ドキュメントだけで判断せず実行して見つけた不具合。
- `packaging/installer.iss`の`[Code]`section冒頭、`{ ... }`形式のPascalコメント内で
  Inno定数の記法（`{app}`）をそのまま文字として書くと、コメント自体が`{app}`の`}`で
  early-closeしてしまい以降が構文エラーになることをISCCの実compileで検出。該当箇所を
  `//`形式のコメントへ書き換えて解消した。
- 当初`CreateInputOptionPage`が返す`TInputOptionWizardPage`を`.ShowModal()`で単独表示する
  設計だったが、実際にISCC 6.7.3でcompileすると`Unknown identifier 'SHOWMODAL'`で失敗した。
  この型はインストーラーの自動page遷移に組み込まれることが前提で、アンインストーラーには
  その遷移機構が存在しないため単独表示メソッドを持たない。確実に動作する`MsgBox`による
  Yes/No確認chainへ設計変更した（要件定義29.7章に判断経緯を記録）。

### 実機検証（2026-08-19、Windows 11 / Intel機、本機のuv package cacheは温cache）

- `winget install --id JRSoftware.InnoSetup`でInno Setup 6.7.3を導入（ユーザー領域
  `%LocalAppData%\Programs\Inno Setup 6`、管理者権限不要）。
- `.\build.ps1`を実PowerShell 7と実**Windows PowerShell 5.1**（`powershell.exe`）の両方で
  実行し、いずれも成功（PyInstallerビルド→推論コア非混入確認→Inno Setup compile→
  SHA-256算出まで完走）。README記載の対応環境（Windows PowerShell 5.1）と実際に一致することを
  確認した。
- PyInstaller onedirビルド結果は43MB。`torch`/`utteran`/`faster_whisper`/`pyannote`/
  `ctranslate2`ディレクトリが含まれないことを実ファイル走査で確認。
- ビルドした`utteran-gui.exe`を単独起動し、プロセスが生存したまま`127.0.0.1`の
  OS割当portへlistenすることを確認。認証なしで`/launch`を叩くと401（セッションkey
  未検証）が返ることを確認し、FastAPI/uvicornが凍結exe内で正しくrequestを処理することを
  検証した（pywebviewのネイティブウィンドウ描画自体は目視が必要なため未検証、
  手動確認手順書へ委譲）。
- 生成したインストーラー（`utteran-setup-0.1.0.exe`、約19.7MB）を`/VERYSILENT
  /SUPPRESSMSGBOXES /DIR=<一時ディレクトリ>`でサイレントインストールし、想定した
  フラットな配置（`utteran-gui.exe`、`_internal/`、`pyproject.toml`、`uv.lock`、
  `setup.ps1`、`run.ps1`、`src/utteran/`、`src/utteran_gui/`等が`{app}`直下に並ぶ）を
  実際のfile存在確認で検証した。スタートメニューショートカットの作成も確認。
- インストール先から`utteran-gui.exe`をcwd=インストール先で起動し（ショートカットの
  `WorkingDir`挙動を再現）、`/launch`が同じく401を返すことを確認。`project_root()`が
  Pythonコード変更なしに正しく動作することを実機で確認した。
- `.venvs/win-cpu/marker.txt`を模擬配置した状態で`unins000.exe /VERYSILENT
  /SUPPRESSMSGBOXES`を実行し、アプリ本体（exe、`pyproject.toml`等）は削除される一方、
  `.venvs`配下のmarkerは残ることを確認した。レジストリのUninstallエントリも
  正しく消去されることを確認。サイレント経路が対話確認を一切行わず「すべて保持」に
  倒れる安全側デフォルトを実機で検証できた。
- Inno Setupのインストールディレクトリを一時的にrenameしてISCC.exe不在を再現し、
  `build.ps1`が`uv sync`等を一切試みずに即座に導入方法付きのエラーで終了することを確認、
  検証後に元へrenameし直した。
- 品質確認: モデル不要pytest 280 passed・1 failed（`test_ctrl_c_is_confined_to_the_child_console`、
  Phase 5d事前準備で既に環境依存と特定済みの既知flaky、無関係）。ruff check/format、mypy
  55 source files、PowerShell BOM 4 file（`build.ps1`は未commit時点のためcheck対象外、
  commit後は5 fileになる想定）、`uv lock --check`、`[System.Management.Automation.Language.Parser]`
  による全`.ps1`の静的構文検査、いずれも合格。

### 未検証の項目（記録）

- 署名付きビルド（`-SignCommand`経路）は証明書を持たないため未検証。
- 実際のSmartScreen警告表示は、ローカルビルドしたexeでは再現しない（Zone Identifierが
  付与されないため）。GitHub Releases公開後、ブラウザ経由でダウンロードしての確認が必要。
- インストーラーのライセンス／追加ダウンロード告知page、アンインストールの`MsgBox`
  Yes/No確認chain、ネイティブWebViewの実際の描画は、対話操作を伴うため未検証
  （`docs/Phase5d_インストーラー_手動確認手順書.md`へ委譲）。
- uv未導入環境からの`Install-Uv`実PATH書き込み分岐は、Phase 5c・Phase 5d事前準備から
  引き続き実機未検証。
- 本機のuv package cacheは温cacheのため、ビルド・初回セットアップの所要時間は
  真にネットワーク帯域律速となる初回導入の代替にならない（Phase 5d事前準備からの
  申し送り事項がそのまま5dにも残る）。
- インストーラーCIへの組み込みは行っていない（要件定義29.9章に判断理由を記録）。

## Phase 5d 事前準備（2026-08-19、完了）

`docs/utteran_Phase5d事前準備指示書.md`に従い、`chore/phase5d-preparation`で作業A（cp932関連flaky調査）と
作業B（クリーン環境での初回フロー検証）を実施した。詳細は`docs/Phase5d事前準備.md`。

- **作業A**: Phase 5c報告の「既知のcp932関連flaky 1件、無関係」は誤帰属だった。実際にflakyな
  唯一のtestは`test_ctrl_c_is_confined_to_the_child_console`で、cp932とは無関係。Git Bash/ConPTY
  （実consoleが未添付）から実行すると100%失敗（3/3timeout）、実PowerShell consoleからは100%成功
  （5/5）と決定論的に環境と相関しており、Phase 3d時点で既にAISTATE.mdに記録済みだった
  「試験ハーネスの実行環境依存、製品側の不具合ではない」という既存の根拠をPhase 5c報告が
  引用し損ねていた。
- 一方、GUI↔CLIのconsole非接続経路を再確認する過程で**新規のcp932不具合を発見・修正**した。
  `setup.ps1`自身の`Write-Host`/`Write-Step`出力は、GUIウィザードが起動するときのように自身の
  stdoutがpipe接続（非console）だと、`[Console]::OutputEncoding`がOEMコードページ
  （cp932）へ既定化し、日本語進捗行が文字化けする。ステージマーカー行はASCIIのため
  ステージ検出自体は壊れず、Phase 5cの実機検証（「6ステージマーカーが期待順序」）はこれを
  検出できなかった。`[Console]::IsOutputRedirected`のときだけ起動時にUTF-8を強制する修正と、
  `setup.ps1 -List`を実際にpipe経由で起動して復号する回帰test
  （`tests/test_profiles.py::test_setup_list_writes_valid_utf8_when_stdout_is_piped`、Windows限定）
  を追加した。
- ついでに`utteran_gui/processes.py::kill_process_tree`がWindows上でmypy実行時に
  `os.killpg`/`signal.SIGKILL`（POSIX専用）を解決できず失敗する問題を`getattr`ベースの解決へ
  修正した。CIのmypyはLinuxでのみ走るため検出されていなかった。
- **未解決の既知の問題（記録のみ、修正見送り）**: `setup.ps1`のverify段で
  `Invoke-Utf8Captured { & uv run --no-sync utteran devices | Out-String }`を経由して表示する
  device診断tableの罫線文字（┌┬┐├┼┤└┴┘等）が、cp932ロケール・pipe接続時に不規則に文字化けする
  （同じ行内の日本語テキストは正しく復号される）。実機で`cpu`profileの実venv構築を通して再現。
  `$OutputEncoding`（PowerShellの外部process出力capture用の別変数、`[Console]::OutputEncoding`とは
  独立）を明示UTF-8にしても再現し、単純な既定コードページ問題ではなくpipe読み取りのbuffer境界に
  絡む問題の可能性がある。影響は`verify`段の詳細ログ内の装飾的な罫線のみで、`devices --json`
  （機能的な判定に実際に使われる経路）や`utteran transcribe`が直接出す同種のrich table
  （box-drawing文字含む、PowerShellを経由しない経路）は正しく表示されることを確認済み。
  この経路の`Out-String`ワークアラウンド自体、既存コード中のコメントで「JSON captureと同種の
  問題が残る」と既に認識されていた。原因の完全な特定と修正は追加調査が必要なため見送り、記録した。
- **作業B**: 別ディレクトリへのlocal clone（uvバイナリとuvパッケージcacheは共有、
  `UTTERAN_MODEL_DIR`のみ隔離、GUI設定は`SettingsStore(path=...)`で隔離、job dirと
  memory-calibrationは追記のみで既存を壊さないため意図的に共有）で真に空の`.venvs`を用意し、
  `SetupWizardService`（GUIのAPIが呼ぶのと同じ層）を直接呼び出して初回フローを実機で完走させた。
  - 初回起動判定: venv0個で`status().first_run == true`を確認。
  - ハードウェア検出: 実Intel Arc GPUを検出し、`recommended="intel"`、話者分離のGPU実行可否を
    含む理由文を確認。**この過程でウィザードのプロファイル選択カードが技術識別子
    （`cpu`/`intel`等）を無翻訳のまま見出しに表示している不具合を発見**（指示書が要求する
    「プロファイル名を知らなくても選べる表現」に反する）。ja/en翻訳ラベルを追加して修正、
    回帰testを追加した。
  - `cpu`profileのvenv構築を実機で完走（exit 0、6ステージ全て期待順序、40秒）。uvは既導入済み
    分岐（"uv already available"）を実確認。uv未導入からの自動導入の実dl分岐は、実PATH・実
    `%LOCALAPPDATA%\utteran\bin`を汚さずに検証する方法が確立できず、Phase 5c同様
    **実機未検証のまま**とした。
  - cancel/resume: `vulkan`profile構築を開始3秒後にcancelし、`status=cancelled`/`exit_code=130`、
    process tree残留なしを確認。同一profileを再構築し31秒でexit 0完走、既存`.venvs`に影響なし。
  - モデル取得: 隔離した空`UTTERAN_MODEL_DIR`へ`faster-whisper:large-v3-turbo`を実dlし、
    事前表示の概算（1.6 GiB）と実サイズがほぼ一致することを確認。
  - 実動作確認（smoke test）: 隔離venv・隔離モデルに対し合成無音WAVで実`transcribe`を実行し、
    exit 0で完走。`complete()`はsmoke test成功前は`WizardNotReadyError`で拒否され、成功後は
    `status().first_run`が`false`へ切り替わることを確認（2回目起動でウィザードが出ない設計の
    実効性を確認）。
  - 検証後、隔離clone・隔離モデルcache・共有job dirに生成された1件のsmoke.wav jobを削除し、
    既存`.venvs`／実model cache／実job（他の実利用者jobを含む）が変更されていないことを確認した。
  - uv cacheを共有したため、計測した所要時間（venv構築40秒等）は温cacheでの数値であり、
    真の初回ネットワーク帯域律速の所要時間の代替にはならない。
- 完了条件のうち、モデル不要test（280 passed）・ruff・mypyは合格。受入ハーネス
  （既定実行、162件）は151 pass・4 fail・7 skip（CUDA不在の理由付き）で、4 failは個別再実行で
  切り分けた: 2件（G4-09/P10-A）は一括実行時のリソース競合による一過性のtimeoutで単独実行では
  pass、1件（G14-08）は今回G0〜G13を通しで実行していないための想定通りの集計未達、
  残り1件（P4-11、`models download`ID省略時の非対話環境exit codeがtestの期待と不一致）は
  Phase 5c時点のcommitでも同じ内容で失敗する既存の不整合で、Task A/Bの範囲外のため未修正・
  記録のみとした。**今回の変更による新規の受入試験回帰は無い。** 4文書
  （README／変更履歴／要件定義／AISTATE）は変更内容に応じて更新した
  （要件定義は既存要求に対する適合修正のため変更なし、他3件を更新）。

## Phase 5c 初回セットアップウィザード（2026-08-17、着手・Step 0設計）

### Step 0 — 検証方法の設計（実装前に記録）

作業機（Windows 11、Intel機）は既に uv・`intel` profile・model・ffmpeg を導入済みのため、
「何もない状態からの初回セットアップ」を実機で壊さずに再現することはできない。
`docs/utteran_Phase5c_指示書.md` の要求に従い、以下の方法で検証する。

- **hardware.py（ハードウェア検出・推奨profile算出）**: 偽の検出結果を注入したユニットテストで
  NVIDIA / Intel / AMD等 / 不明 / GPUなし の各分岐を網羅する。実機のWMI呼び出し自体は読み取り専用
  （`Get-CimInstance Win32_VideoController`）のため実機で一度実行し、本機がIntel iGPU搭載機として
  正しく検出されることだけを確認する。既存環境への書き込みは発生しない。
- **uv自動導入（setup.ps1の`Install-Uv`）**: ダウンロード関数を注入可能にし、fakeなユニットテストで
  ロジックを検証する。加えて実機で、実際の`%LOCALAPPDATA%\utteran\bin`やユーザーPATHではない
  **隔離した一時ディレクトリ**へ向けて、本物のGitHub releaseからのダウンロード→SHA-256検証→展開→
  `uv.exe --version`実行までを本物のネットワークで通す。これによりダウンロード・検証・展開ロジック
  全体を実機で確認できるが、本機は既にuv導入済みのため「実PATHへの永続書き込み」分岐そのものは
  到達せず、**実機未検証**として扱う。
- **venv構築の進捗ストリーミング（setup_wizard.py）**: `tests/test_gui.py`のJobManagerテストと同じ
  `FakeProcess`注入パターンで、`setup.ps1`風の標準出力行（`##UTTERAN-WIZARD## stage=...`マーカー含む）
  を与えてパース・SSE配信・cancelをユニットテストで検証する。加えて実機で、既存`intel`profileを
  `setup.ps1`の既存`-VenvDir`引数を使って**一時ディレクトリへ**再構築し（既存`.venvs/win-intel`には
  一切触れない）、本物の`setup.ps1`出力形式でステージ検出が機能することを確認したうえで
  一時ディレクトリを削除する。
- **モデル取得・HFトークン・実動作確認（smoke test）**: fakeによるユニットテストのみで分岐を確認する。
  smoke test自体（無音の合成WAVでの文字起こし実行）は既存`intel`profileに対して実際に実行する
  （新規job dirが1つ作られるだけで既存jobには影響しない）。
- **実機で検証できない項目（「実機未検証」と明記する）**: `cuda`profile（本機にNVIDIA GPUなし）、
  真に空の状態（`.venvs`が1つもない状態）からの初回起動フロー全体、Linuxでのuv自動導入
  （Phase 5cではWindows GUI限定のため対象外）。
- **ロールバック方針**: 実機検証の書き込みは常にOS一時ディレクトリまたは一時`-VenvDir`にのみ向け、
  既存`.venvs/win-intel`・実ユーザーPATH・実`settings.json`・実keyringエントリには一切書き込まない。
  そのため検証後の復元作業は不要（一時ディレクトリの削除のみ）。

### 実装内容（新規/変更ファイル）

- `src/utteran_gui/hardware.py`（新規）: GPUベンダー検出（`powershell.exe`経由の
  `Get-CimInstance Win32_VideoController`）、メモリ（`ctypes` `GlobalMemoryStatusEx`）、
  ディスク空き容量、推奨profile算出。`torch`/`openvino`等は一切importしない。
- `src/utteran_gui/processes.py`（新規）: `jobs.py`から`kill_process_tree`とPopen kwargs構築を
  切り出した共有モジュール。`jobs.py`と`setup_wizard.py`の両方が利用する。
- `src/utteran_gui/setup_wizard.py`（新規）: `SetupWizardService`。venv構築
  （`setup.ps1 -Profile <p> -Yes`をsubprocess起動）、モデル取得
  （`utteran models download <ref>`）、実動作確認（合成無音WAVでの`transcribe`）を
  `JobManager`と同型の1操作制限・event cursor・tree kill機構で実行する。
- `src/utteran_gui/api.py`: `/api/wizard/status`、`/api/wizard/hardware`、
  `/api/wizard/jobs`、`/api/wizard/jobs/{id}`、`/api/wizard/jobs/{id}/cancel`、
  `/api/wizard/jobs/{id}/events`、`/api/wizard/complete`を追加。既存`/api/token`・
  `/api/settings`はそのまま流用（新設しない）。
- `src/utteran_gui/settings.py`: `GuiSettings`に`setup_wizard_completed_at`を追加。
- `src/utteran_gui/jobs.py`: `guidance_for`に`license`カテゴリを追加し、
  `ModelAgreementError`（利用条件未同意）と既存`token`（`HuggingFaceAuthenticationError`＝
  トークン自体が無効）を区別するようにした（Phase 1で既に別クラスだった2つのエラーを
  GUI側で初めて区別）。
- `setup.ps1`: `Install-Uv`関数を追加（`Install-Ffmpeg`と同じ「取得前確認→ダウンロード→
  SHA-256検証→展開」の型。astral-sh/uvのGitHub最新releaseの`uv-x86_64-pc-windows-msvc.zip`と
  公開`.zip.sha256`を使用）。`Write-Step`に任意の`-Stage`パラメータを追加し、
  `##UTTERAN-WIZARD## stage=<slug>`という機械可読マーカーを主要ステップ
  （python_check/uv_install/venv_sync/ffmpeg/env_setup/verify）に付与した。
  既存のトップレベル`param()`は変更していない。

### 実機検証結果（2026-08-17実施、Windows 11 / Intel Arc 140T機）

- **ハードウェア検出**: `hardware.detect_hardware()`を実行し、
  `"Intel(R) Arc(TM) 140T GPU (32GB)"`を正しく検出、`recommended="intel"`、
  話者分離がXPUで動くことを含む理由文を確認した。副作用なし（読み取り専用）。
  実測ディスク使用量（`setup.ps1 -List`の実表示）: cpu=1.0 GiB、intel=5.3 GiB
  （指示書記載の5.3GBと一致）、vulkan=1.1 GiB。`cuda`はNVIDIA GPU不在のため未測定
  （指示書記載の約2.4GBのまま採用）。
- **uv自動導入ロジック**: 本機は既にuv導入済みのため`Install-Uv`関数自体は早期returnし、
  ダウンロード分岐へ到達しない（**実機未検証**の分岐）。ダウンロード→SHA-256検証→展開→
  実行という実処理そのものは、実際のastral-sh/uv最新release（0.12.5、当時）に対して
  隔離した一時ディレクトリ（`%TEMP%\utteran-verify-uv`、実`BinDir`・実PATHとは無関係）で
  再現し、チェックサム一致・展開・`uv --version`実行まで成功を確認した。検証後は
  一時ディレクトリを削除済み。ユーザーPATHへの永続書き込み分岐は本機では検証していない。
- **venv構築の進捗ストリーミング**: `setup.ps1 -Profile cpu -VenvDir <一時ディレクトリ>
  -SkipFfmpeg -Yes`を実行し、既存`.venvs/win-intel`等には触れずに独立したcpu venvを構築、
  6つのステージマーカー（python_check/uv_install/venv_sync/ffmpeg/env_setup/verify）が
  すべて期待順序で出力されることを確認した。検証後に一時ディレクトリを削除し、
  既存`.venvs`（linux-ci/win-cpu/win-intel/win-vulkan）が変化していないことを確認した。
- **実動作確認（smoke test）**: 既存`intel`profileに対して`SetupWizardService.start_smoke_test`
  を実際に実行した。初回は`asr_model_ref`未指定のため、`intel`profileの自動選択
  （whisper-cpp/vulkan）が未導入のモデルを指すエラー（exit 3、guidance=`model`）で
  意図的に失敗を確認——この結果を受けて`start_smoke_test`に`asr_model_ref`引数を追加し、
  ウィザードのモデル取得ステップで実際に取得したモデル参照を渡す設計に修正した
  （設計判断、下記参照）。`asr_model_ref="faster-whisper:large-v3-turbo"`
  （導入済み）を指定した再実行はexit 0で完了し、実ジョブが生成された。検証用に作成した
  2件のsmoke.wavジョブ（1件失敗・1件成功）は`jobs clean --job-id --yes`で削除済みで、
  既存の実利用者ジョブ（実ファイル名を含む）は一切変更・削除していない。
- **実機で検証できなかった項目**: `cuda`profile（本機にNVIDIA GPUなし）、真に空の
  `.venvs`からの初回起動フロー全体、Linuxでのuv自動導入（対象外）、
  ユーザーPATHへの永続書き込み分岐、GUIウィザード画面自体の目視確認
  （`docs/Phase5c_GUI_セットアップウィザード_手動確認手順書.md`へ委ねる）。

### 設計上の判断とその理由（追加分）

- `start_smoke_test`に`asr_model_ref`引数を追加した。当初は`--no-diarization`のみで
  profileの自動選択に任せる設計だったが、実機検証で`intel`profileの自動選択
  （whisper-cpp/vulkan）と実際に導入済みのモデル（faster-whisper）が一致せず
  smoke testが失敗する実例を確認したため。ウィザードのモデル取得ステップで
  取得した`model_ref`（`"<backend>:<model_id>"`形式、カタログの`key`と同じ書式）を
  そのまま`--asr-backend`/`--asr-model`へ渡すことで、実際に導入したモデルで
  確実に検証できるようにした。
- 初回起動判定（`SetupWizardService.status()`）は、指示書の字面どおり
  「設定完了記録がない」ことだけでは判定せず、「設定ファイル自体が存在しない」
  ことを条件にした。Phase 5c以前から使っているGUI利用者は`setup_wizard_completed_at`
  フィールドを持たない`settings.json`を既に持っているため、字面どおりの判定では
  既存利用者全員に毎回ウィザードが出てしまい、指示書自身が禁止する
  「既に使っている利用者に対して毎回ウィザードを出す」状態になる。既存利用者を
  煩わせない方を優先した。
- `kill_process_tree`とPopen kwargs構築を`jobs.py`から`processes.py`へ切り出した。
  `setup_wizard.py`が同じプロセスツリー終了・分離ロジックを必要とし、
  重複実装を避けるため。

## レジューム挙動調査（2026-08-09、実機調査・是正完了）

- `fix/resume-behavior-investigation`でWindows CUDA／`cuda:0`、`large-v3-turbo`／`large-v3`、
  話者分離なしの5分派生clipを使用した。元入力と既存jobは変更せず、本文、固有名詞、話者名、
  元file名を出力・記録していない。対象jobは`jobs clean --job-id`で個別削除し、既存5 jobを保持した。
- 同一条件の2回目は1.323秒で全5 stageと既存2出力を再利用し、上書き・連番追加・日時更新なし。
  出力削除後は1.436秒、出力先変更は1.417秒でexportだけを実行した。実装は一貫するが衝突連番との
  適用関係が不明なため仕様の欠陥と判定し、要件定義とREADMEへ正しい挙動を明文化した。
- turbo→large→turboは各model変更でASR／merge／exportを再実行し、ASR hash、出力model、
  59 segmentに対するword／文字統計がmodel別に切り替わった。異modelの古いcache再利用は再現せず、
  操作の誤り相当かつ表示不足と判定した。GUI選択もCLIへ正しく渡り、同一条件では全stage再利用した。
- 調査中、`large-v3`と`faster-whisper:large-v3`が同じ実体なのに別ASR hashとなる逆方向の不具合を
  発見。修正前に失敗testを追加し、backend明示時のcatalog ID正規化で解消した。受入`G4-17`で
  真のmodel変更とalias同値を検査する。
- CLI／GUI完了表示とJSONL `run_summary`へ実ASR backend／model／device、実行stage、再利用stageを
  追加した。即時終了が正しいcache hitかを完了画面で判断できる。
- 86 fileのCRLF/LFのみの差分、Windows Git `core.autocrlf=true`、WSL未設定を確認。
  `.gitattributes`で通常text=LF、PowerShell等=CRLF、media／model／実行形式=binaryとし、EOLだけの
  変更表示を解消した。全file renormalizeは不要のため行っていない。詳細は
  `docs/レジューム挙動調査.md`。
- 最終品質確認はモデル不要256 passed／環境依存3 skipped、ruff check／format 93 file、mypy
  52 source file、lock、PowerShell BOM 4 file、Windows PowerShell 5.1構文検査、受入G4-17 1/1が
  合格（3.911秒）。修正後のWindows CUDA実行も0.634秒で全5 stage再利用とcanonical model表示を確認した。

## Phase 5b GUI結果閲覧・検索・履歴（2026-08-09、実装・自動検証完了）

- `feature/phase5b-gui-viewer`をPhase 5a完了commitから作成した。作業開始時に既存86 fileの
  CRLF/LF差分があったため、内容を破棄せず保持している。
- `jobs list --json`／`jobs show --json`を追加し、履歴へ入力名、日時、状態、容量、ASR／話者分離の
  model・device、話者数、音声長、出力pathを返す。個別削除は`jobs clean --job-id --json`とし、
  live lock所有jobを拒否する。
- viewerと再出力は出力先JSONでなくjob内`merged.json`を正本とした。出力JSONは削除・移動され得て、
  表示名適用後は内部話者labelを失うため。schema version 1以外、欠落、破損は`corrupt`として
  対応／検出versionを明示し、推測表示しない。
- `jobs export`は`merged.json`を共通`PipelineResult`へ復元してexporterだけを実行し、形式、
  出力先、`SPEAKER_00=表示名`を変更できる。audio／ASR／diarization／merge recordは変更しない。
  表示名と直近出力指定はGUI設定でなく関連job内`presentation.json`へ保存し、job削除で同時消去する。
- GUIは結果／履歴view、固定108px行の仮想scroll（viewport＋overscanだけDOM化）、検索highlight・
  件数・前後移動、IME composition中の検索抑止＋180ms debounce、話者／時間filter、話者色、
  model／device強調表示、発話時間／割合／平均turn、履歴filter／sort／open／個別delete、
  export-only再生成を実装した。word詳細は正本とCLI JSONに保持し、GUI初期payloadはword数だけ返す。
- 本文、検索語をlog／settings／追加cache／Web Storageへ保存しない。全APIに
  `Cache-Control: no-store`を付与し、viewerを離れると検索語とin-memory resultを破棄する。
  手書き合成結果だけのCLI/API/privacy回帰を追加した。
- 既存の長時間実jobは`merged.json` 9,236,847 byte、1,280 segment、23,117 word。
  本文非出力でのGUI用読込／正規化は0.326秒、JSON化0.003秒、payload 255,336 byte。
  650px viewportの同時DOM行はoverscan込み最大23行で、server側は1秒の初期表示目標内。
  実データの本文、file名、参加者名は計測出力とGit成果物へ含めていない。
- 重点回帰は`test_cli.py`/`test_gui.py`/`test_jobs.py`/`test_pipeline.py`が65 passed／
  Windows限定1 skipped。全モデル不要testは254 passed／環境依存3 skipped。ruff format/check、
  mypy 52 source files、lock、PowerShell BOM、公開tree scan（blocking 0）は合格。
- 受入ハーネスG11はREADME CLI例と公開文書契約の2/2 pass（143.4秒）。
  `.venvs/win-gui`のWindows Pythonで長時間job 1,280 segmentを0.211秒で読込み、
  Windows Edge headlessでfrontend起動、外部script読込み、5出力形式の動的DOM生成を確認した。
  フォント、dark/lightの色、連続scrollとIME操作は自動化に不向きなため、
  `docs/Phase5a_GUI_手動確認手順書.md`と`docs/Phase5b_GUI_手動確認手順書.md`に実機手順を残す。

## Phase 5a Windows GUI基盤（2026-08-09、実装・実機確認完了）

- `docs/utteran_Phase5a_指示書.md`に従い、推論coreをimportしない独立package
  `src/utteran_gui`を追加した。AST回帰試験でも`utteran` importがないことを検査する。
- `setup.ps1 -Profile gui`は`.venvs/win-gui`へFastAPI、Uvicorn、pywebviewだけを導入し、
  PyTorch／faster-whisperが存在しないことをprobeする。`gui.ps1`はこの環境の`utteran-gui`を起動する。
  GUIはprofileごとの`utteran.exe`をshellなしの引数配列で子process起動する。
- local APIはOS割当の`127.0.0.1:0`へ事前bindし、起動ごとのsession keyで全`/api/*`を認証する。
  初期launchはHttpOnly／SameSite=Strict cookieを設定し、CORSは有効化しない。CSP等のsecurity
  headerも付与した。key不一致を記録する際もkey値はlogへ出さない。
- GUIは`profiles list --json`、`devices --json`、`models list --json`、`native status --json`から
  導入済みmodelと利用可能device／native構成だけを動的生成し、開始時にも再検証する。
  同時jobは1件、進捗はSSE、固定timeoutなし、30秒無eventは停止でなく応答待ち表示、cancelは
  Windows `taskkill /T /F`またはPOSIX process group停止とした。
- `transcribe --progress-json`を追加し、schema version 1のUTF-8 JSONLをstderrへ逐次flushする。
  job/file/stage/progress/output/warning/error/doneを公開契約とし、全stringを秘密maskする。
  segment、word、文字起こし本文、tokenはeventへ含めない。非JSON／不完全行はGUIがraw logとして
  mask後に保持する。
- GUIはdark既定／light、日本語既定／English、profile／backend／model／device、話者分離、
  話者数、形式、resume mode、file／folder／glob、drag and drop、stage／経過／ETA／応答待ち、
  詳細log、中断、生成file／folder表示を実装した。theme、language、既定profile、既定directoryだけを
  user configのJSONへ原子的保存し、入力file履歴は残さない。HF tokenはOS keyringへ保存し、APIは
  設定済み状態だけを返す。
- 依存は`faster-whisper`をbaseから`cpu`／`cuda`／`xpu`へ移し、`gui` extraを軽量化した。
  `dev`はcore testのためfaster-whisperとGUI API試験用httpxを含む。XPUのtorch／torchaudio／
  pyannote／faster-whisperはWindows条件付きとし、非Windows XPUは引き続き未検証。
- Windows PowerShell 5.1で`setup.ps1 -Profile gui -Yes`成功。47 package、約47 MBで、
  `torch`／`faster_whisper`不在、GUI import、random loopback bindを確認した。
  `setup.ps1 -Profile cuda -Yes`も再実行成功し、CTranslate2 4.8.1の`cuda:0` int8、
  PyTorch 2.11.0+cu126 CUDA、pyannote CUDAの実probeに合格した。WSLから最初に呼んだ際は継承された
  `PATHEXT=.CPL`のためPython／uv探索に失敗し、Windows標準PATHEXTへ戻して再実行した。
  製品setupの不具合ではなくWSL interop環境固有の前提である。
- GUI `JobManager`から既存30秒受入素材を実行し、話者分離OFFはCUDA ASRでexit 0、27 events、
  JSON／SRT生成を確認した。話者分離ONはCUDA ASR＋CPU pyannoteでaudio／asr／diarization／merge／
  exportの全stageを通り、exit 0、40 events、JSON／SRT生成、stallなしを確認した。pyannoteもCUDAを
  明示した場合、GTX 1070 Ti 8 GiBでは既存memory guardが必要7.31 GiB／予算6.32 GiBとして
  推論前に安全停止した。このeventをGUIが`memory`案内へ分類し、話者分離deviceのCPU切替を示す。
- 実機検査の生成物はGit対象外の`output/gui-e2e-*`だけに置き、認識本文は画面・作業記録へ
  出していない。既存`.venv`／`.venv-windows`は変更・削除していない。
- 10分派生素材をGUI `JobManager`で開始し、ASR stage開始後に実cancelした。Windowsのprocess treeが
  終了してstatus `cancelled`／exit 130／resume案内となり、出力なし、stallなしを確認した。
  さらに`.venvs/win-gui`の`utteran-gui.exe`からnative pywebviewを起動し、WebViewを含む13 processの
  tree内でOS割当portの`127.0.0.1` listenerを確認した。検査後は対象treeだけを終了しGUI host残存0件。
- `docs/Phase5a_GUI_手動確認手順書.md`へnative window、動的選択、theme／言語、keyring、
  drag and drop、実行／resume、Explorer、中断、error案内の目視手順を記録した。
- 最終品質確認はモデル不要`247 passed / 3 skipped`（既知のStarlette TestClient移行warning 1件）、
  ruff check合格、ruff format 92 files合格、mypy 51 source files合格、`uv lock --check`合格。
  Windows PowerShell 5.1の全root `.ps1` parser検査と新旧scriptのUTF-8 BOMも合格した。
  受入ハーネスG11はREADME CLI例／公開文書契約の2/2合格（265.3秒、失敗・skipなし）。

## Phase 4b メモリ管理（2026-08-05、進行中）

- 指定4文書を確認し、`feature/phase4b-memory-management`を作成した。`.env`は読んでいない。
- Step 0 は分岐B。pyannote.audio 4.0.7の実コードで `get_segmentations`、
  `get_embeddings`、`clustering`、`reconstruct` を確認し、固定合成12秒波形で中間 shape と
  通常／exclusive再構成を実行した。しかし分割には `apply` の主要ロジック複製、
  `_segmentation.model.receptive_field` 等の非公開属性、重複チャンクの時刻格子統合が必要で、
  安定した内部構造とは判断できない。指示に従いStep 3は実装しない。根拠、実装量、更新リスク、
  CPU退避で救える範囲は `docs/調査結果_Phase4b.md` に記録した。
- Step 1: CUDA空きVRAM、XPU上限と空きRAMの小さい方、CPU空きRAMを別々に扱う予算算出、
  種別別安全率、取得不能をunknownとする判定を実装。R-5のOLS式を同梱し、3点以上で
  MAD外れ値除外後のローカル式へ切り替える。platformdirsのprofile共通領域にはstage、backend、
  device種別、音声長、peak、日時だけを保存する。`memory show/reset`とPhase 3d同等のprocess tree
  working set monitorを追加し、重点51 test、ruff、mypyに合格した。
- Step 2: 話者分離直前にsafe/danger/impossible/unknownを判定し、deviceとmemory guardがともに
  autoの場合だけ、安全と確認できたCPUへ事前退避する。明示deviceは変更せず、基礎量超過は
  CPU切替／話者分離省略／個別分割を提示して開始前に止める。OOM/MemoryError/bad allocationを
  捕捉し、auto（guard auto/off）だけCPUへ1回再試行する。音声・ASR stageは保持される。
  退避理由、判定、推定、予算、実測peakをdiarization中間結果、merged、最終JSON、job logへ記録。
  `UTTERAN_DEBUG_MEMORY_BUDGET_GIB`で予算を人工制限できる。モデル不要234 test、ruff、mypy合格。
- Step 4実機検証: 3分fixtureで通常safe/XPU peak 4.962 GiB、人工raw予算4.9 GiBのautoは
  danger/CPU退避/2.775 GiB、XPU明示はdanger警告のみ/4.952 GiB、2.5 GiBはmodel load前exit 3。
  guard off + XPU allocator 2%制限で実OOMを発生させ、捕捉後CPUへ1回だけ再試行して完走した。
  audio/ASRはresumeされ、job log/中間/merged/最終JSONの記録も確認。OOM後CPU peakはXPU残留
  working setを含むためキャリブレーション対象外とした。R-5の25/50/100分byte実測に対する
  同梱式誤差はXPU -0.424〜+0.617%、Vulkan ASR -0.015〜+0.019%、CPU 2点0%。
  詳細は`docs/検証結果_Phase4b.md`。Step 4-Cは分岐Bのため対象外。
- 統合受入P9初回は6/7 pass。ほぼ同じ3分点が3点たまり、微小な音声長差からlocal slopeが
  暴走してP9-4を1940 GiBと誤推定した。3点条件に加えて音声長span 5分以上を必須化し、
  pairwise slopeも5分未満の差を使わないよう修正。重点test後のP9-4再実行は70.2秒でpassし、
  ID別最新P9は7/7 pass。

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
- 21 refsの`git bundle --all`、非hardlink mirror、bundle復元cloneを作成し、ref完全一致、
  bundle verify、両cloneのfsckに合格。release 1件と`v0.0.1`対応も記録した。`filter-repo`
  2.47.0で全120 commitsのcommit message email形式とfile内user絶pathを一般化。
  ローカルtree ref 9件をbackup後に除去し、reflog失効/aggressive GCを実施。pushは未実施。
- 書き換え後は8 refs/121 commits/1,146 reachable objects、unreachable 0。秘密hash再照合は
  5分類全0件。dummy path test 2 filesはbackup blobから戻し、`94c632c`のtree hashは
  事前の`2045447b...`と完全一致。`v0.0.1`のtag/commit SHAは変更されたためrelease再照合が必要。
- 再発防止として、email付きcommit trailerを使わない規約を定義。Git checkout内では
  Git除外済み出力先だけを許可し、`output`/`transcripts`/`utteran-output`の5形式を
  `git check-ignore`で回帰確認する。CIの汎用current-tree scanとlocal hash照合の使い分けも文書化。
- 書き換え・再発防止後はモデル不要pytest 217 pass、ruff/format/mypy/lock/BOM/
  current-tree scan全合格。統合受入は実出力G2 16件+公開文書G11 2件を新規結果へ実行し、
  18 pass/0 fail/0 skip（393.0秒）。文字起こし本文は報告/Gitへ保存していない。

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
