# Phase 5d 事前準備 報告

`docs/utteran_Phase5d事前準備指示書.md`に従い、`chore/phase5d-preparation`ブランチで実施した。
実データは検証に使用していない（合成無音WAVのみ）。`.env`は読み取っていない。

## 作業A — cp932関連flakyの調査

### 結論

Phase 5cの「pytest 279 passed（既知のcp932関連flaky 1件のみ、無関係）」という報告は**誤帰属**
だった。実際に唯一flakyだったtestはcp932/文字コードとは無関係で、試験ハーネスの実行環境依存
だった。ただし調査の過程で、報告が「無関係」と判定した領域そのもの（GUI↔CLIのconsole非接続
経路）に**新規のcp932関連不具合**を発見し、修正した。

### 1. 「既知のflaky」の正体

`pytest -m "not requires_model"`をGit Bash（cp932ロケール、`PYTHONIOENCODING`未設定、実Win32
consoleが未添付）から実行すると、`1 failed, 279 passed`となり件数がPhase 5c報告と一致した。
失敗していたのは`tests/test_acceptance_scenarios.py::test_ctrl_c_is_confined_to_the_child_console`。

再現性を確認するため同一testを条件を変えて繰り返し実行した。

| 実行環境 | 結果 |
|---|---|
| Git Bash（実console非添付） | 3/3 失敗（30秒timeout） |
| Windows PowerShell 5.1（実console） | 5/5 成功（約1.5秒） |

100%決定論的に環境と相関しており、「たまに失敗する」というflakyな性質ではなかった。
このtestは`tools/acceptance/scenarios.py`のCtrl+C配送機構（Windows console制御API）を使うため、
実console添付が前提であり、Git Bash／ConPTY環境では機構自体が成立しない。この事実は
Phase 3d時点で既にAISTATE.mdへ「製品側の不具合ではなく、試験ハーネスの実行環境依存」と
記録済みだった。Phase 5c報告はこの既存記録を引用せず、「cp932関連」という誤った分類のまま
「無関係」と断定していた。

**判定: 環境依存で製品に影響しない。** 根拠は上記の通り確定的に再現し、原因（実console
非添付時のCtrl+C配送不能）も特定済み。修正は行わず、本報告と既存のAISTATE.md記録を
唯一の根拠として残す。

### 2. GUI↔CLIのconsole非接続経路の再確認

指示書が重点確認を求めた経路を、cp932ロケール・`PYTHONIOENCODING`未設定のGit Bash環境で
実際にpipe経由で確認した。

| 経路 | 結果 |
|---|---|
| `devices --json`（pipe経由） | 有効なUTF-8 JSON、日本語含む値を正しく復号（Phase 4a修正が機能） |
| `jobs list --json`（pipe経由） | 同上、正常 |
| GUI→CLI subprocess（`utteran_gui/processes.py`） | `encoding="utf-8"`明示＋`PYTHONIOENCODING`環境変数注入で問題なし |
| **`setup.ps1`自身のWrite-Host/Write-Step出力（pipe経由）** | **不具合を発見（下記参照）** |

### 3. 発見した不具合と対応

#### 3-1. `setup.ps1`のWrite-Host出力がpipe接続時にcp932化する（修正済み）

GUIのセットアップウィザード（`SetupWizardService.start_venv_build`）は`powershell.exe -File
setup.ps1 ...`をstdout=pipeで起動する。Windows PowerShell 5.1は、自身のstdoutが実consoleでなく
pipe接続のとき、`[Console]::OutputEncoding`をOEMコードページ（本機ではcp932）へ既定化する。
`setup.ps1`は`Invoke-Utf8Captured`関数内でのみ一時的に`[Console]::OutputEncoding`をUTF-8へ
上書き・復元しており、`Write-Step`が呼ぶ`Write-Host`本体はこのスコープの外にあるため、
日本語を含む進捗メッセージ（例:「venv ルート: ...」「作成済み」等）がcp932バイト列として
出力される。GUI側（`utteran_gui/processes.py::build_popen_kwargs`）はこれをUTF-8として復号する
ため、文字化けする。

機械可読なステージマーカー行（`##UTTERAN-WIZARD## stage=...`）はASCIIのみのため影響を受けず、
Phase 5cの実機検証（「6つのステージマーカーが期待順序で出力される」確認）はこの不具合を
検出できなかった。

**再現手順**: `subprocess.run(["powershell.exe", "-File", "setup.ps1", "-List"], capture_output=True)`
を`PYTHONIOENCODING`未設定のcp932ロケール環境で実行し、stdoutをUTF-8で復号すると
`UnicodeDecodeError`（cp932バイトが混入するため）。

**修正**: `setup.ps1`冒頭で`[Console]::IsOutputRedirected`が真のときだけ
`[Console]::OutputEncoding = UTF8`をスクリプト全体に適用するようにした。対話console実行
（実際の利用者が`.\setup.ps1`を直接叩く場合）には一切影響しない。

**回帰test**: `tests/test_profiles.py::test_setup_list_writes_valid_utf8_when_stdout_is_piped`
（Windows限定、実`powershell.exe`を起動する生きたtest）。修正前は実際に失敗することを確認済み。

**CIがこの種の問題を検出できるか**: できない。CIの`windows-tests`ジョブは`setup.ps1 -List`を
同種のpipe経由で起動するstepを既に持つが（`.github/workflows/ci.yml`）、(1) GitHub Actionsの
Windows runnerはcp932ロケールではないため今回のバイト列不一致がそもそも起きにくく、
(2) そのstep自体は`ParserError`文字列の有無しか検査しておらず、内容の文字コード正しさを
検証していない。そのため、今回追加した回帰testはロケールに依存しない形で（cp932かどうかに
関わらず、pipe接続時のraw byteをUTF-8として復号できるかを直接検証する）実装し、CIの
`windows-tests`ジョブが実行する標準pytestスイートに組み込むことで、今後の同種の退行を
検出できるようにした。

#### 3-2. `verify`段のdevice診断tableの罫線文字が文字化けする（未修正・記録のみ）

`cpu`profileの実venv構築を隔離clone環境で実行した際、`verify`段で表示される`utteran devices`の
診断table（`Invoke-Utf8Captured { & uv run --no-sync utteran devices | Out-String }`経由）の
罫線文字（`┌┬┐├┼┤└┴┘`）が不規則に文字化けすることを発見した。同じ行内の日本語テキスト
（「項目」「状態」「詳細」等）やCPU/GPU名は正しく復号されており、罫線文字だけが影響を受ける。

切り分けのため、`$OutputEncoding`（PowerShellが外部processの出力をpipe capture する際に使う
別の設定値。`[Console]::OutputEncoding`とは独立）を明示的にUTF-8へ設定した状態でも再現する
最小再現を作成したが、文字化けする文字の位置・パターンが試行ごとに異なり、単純な既定
コードページの取り違えでは説明がつかない。pipe読み取りのbuffer境界にUTF-8マルチバイト列が
またがることによる復号破損の可能性がある。

一方、同じ罫線文字を含むrich tableでも、PowerShellを経由せずGUIが直接`utteran.exe transcribe`
を起動して受け取る出力（smoke testの完了サマリ表）は、実機検証で常に正しく表示された。
つまり問題はPowerShellが外部processの出力をpipeでcaptureする経路に限定される。

**影響範囲**: `verify`段の詳細ログ内の装飾的な罫線のみ。`devices --json`
（実際にprofile検証の合否判定に使われる経路）や、ステージ判定・完了判定には一切影響しない。
実機検証（smoke testまで完走）でも機能上の問題は発生していない。

**判定: 修正せず記録**とした。原因（pipe capture経路の詳細な破損メカニズム）の完全な特定に
追加調査が必要な一方、影響が診断ログの装飾文字のみに限定される低リスクな問題であり、
不確実な理解のまま`Invoke-Utf8Captured`の挙動を変更すると、既に動作しているJapanese文字列の
復号（今回確認した通り正しく動く）を壊すリスクの方が大きいと判断した。既存コード中の
コメント（`setup.ps1`の`$DeviceDisplayText`周辺）も同種の限界を認識した記述をしており、
今回の発見はその既知の未解決問題の具体的な再現・文書化にあたる。

#### 3-3. GUIウィザードのプロファイル選択カードが技術識別子を無翻訳表示（修正済み）

作業Bのハードウェア検出確認中に発見。`wizardProfileCard()`（`src/utteran_gui/web/app.js`）が
カードの見出しに`alternative.profile`（`"cpu"`/`"intel"`/`"cuda"`/`"vulkan"`）をそのまま
表示しており、翻訳されていなかった。同じカード内の「文字起こしの高速化」「話者分離のGPU実行」
等は正しく翻訳されているため、見出しだけが技術用語のまま残っていた。指示書の検証項目2
「プロファイル名を知らなくても選べる表現になっている」に反する。

**修正**: `i18n.js`へ`wizardProfileCpu`/`Cuda`/`Intel`/`Vulkan`（ja/en）を追加し、
`app.js`に`wizardProfileLabel()`を追加してカード見出しに使用。未知のprofileは従来通り
生の識別子へfallbackする。回帰testを`tests/test_gui.py`へ追加した。

#### 3-4. `utteran_gui/processes.py`がWindows上でmypy失敗（修正済み）

`kill_process_tree`のPOSIX分岐が`os.killpg`/`signal.SIGKILL`を無条件参照しており、
typeshedのwin32向けstubには存在しないため、Windows上で`mypy`を実行すると失敗する
（CIの`mypy`ジョブはLinuxでのみ動くため、CI自体は合格していた）。`getattr`ベースの解決へ
修正し、Phase 4a時点の同種問題（`subprocess.CREATE_NEW_PROCESS_GROUP`）と同じ手法で対応した。

## 作業B — クリーン環境での初回フロー検証

### 検証環境の構成

方針は「別ディレクトリへclone」を採用した。理由: 本機は`uv`本体・`intel`/`cpu`/`vulkan`
profile・ffmpeg・実model cache・実jobを既に保持しており、これらを壊さずに「まっさら」を
作るには、ディスクレベルの分離（別clone）が最も低リスクかつ迅速。仮想マシンは最も確実だが、
本検証はGUIのservice層（`SetupWizardService`）をPython経由で直接駆動する方式（下記）を
採ったため、OSレベルの完全隔離までは必要と判断しなかった。

| 対象 | 扱い | 理由 |
|---|---|---|
| `.venvs`（venvそのもの） | **隔離**（新規clone独自のディレクトリ） | 検証の主目的そのもの |
| `UTTERAN_MODEL_DIR`（モデルcache） | **隔離**（空の一時ディレクトリを環境変数で指定） | 環境変数で分離可能と指示書が明示。実dlの所要時間・サイズ表示を正しく検証するため |
| GUI設定（`settings.json`） | **隔離**（`SettingsStore(path=...)`を明示指定） | 初回起動判定はこのfile不在に依存するため必須 |
| uv本体 | 共有 | ユーザー領域に1つのみ。指示書も分離困難な例として想定 |
| uvのpackage cache | 共有 | 分離するとvenv構築の所要時間が代表値にならず、かつ既存uv cacheを壊すリスクなく共有できるため |
| ffmpeg（アプリケーションデータ配下） | 共有 | 指示書が分離困難と明示する対象 |
| job dir・memory-calibrationのデータ | **意図的に共有** | 追記のみで既存データを書き換えない。分離の複雑さに見合う追加リスクがないと判断。生成した1件のjobは検証後に削除 |

**重要な副作用**: uvのpackage cacheを共有したため、venv構築で計測した所要時間（cpu profile
約40秒）は温cache（既にダウンロード済みのwheelを再利用）での数値であり、真にネットワーク
帯域律速となる初回インストールの体感時間の代替にはならない。この点は5dへの申し送り事項とする。

### 検証方法

GUIの`/api/wizard/*`エンドポイントが内部で呼ぶのと同じ`SetupWizardService`
（`src/utteran_gui/setup_wizard.py`）を、本機の既存`.venv`のPythonから直接importし、
`repo_root`にだけ隔離clone環境を渡して駆動した。ネイティブWebViewの目視確認は
`docs/Phase5c_GUI_セットアップウィザード_手動確認手順書.md`に既に委譲されているため
（Phase 5c時点で「実機未検証」と明記されていたのはservice層の空`.venvs`からの通し実行
そのものであり、画面描画ではない）、今回はその欠落部分を実処理で埋めることに集中した。

### 検証項目ごとの結果

| # | 項目 | 結果 |
|---|---|---|
| 1 | venv0個の状態でウィザードが起動する | ✅ `status().first_run == true`を確認 |
| 2 | ハードウェア検出・推奨・話者分離GPU可否が読み取れる／profile名を知らなくても選べる | ✅ ただし後者に不具合発見・修正（3-3） |
| 3 | uv: 既導入時は何もしない／未導入時の導入 | ✅ 既導入分岐を実確認。未導入分岐は実機未検証（後述） |
| 4 | venv構築: 進捗表示・無反応時間なし・具体的な内容・中断・再開 | ✅ 6ステージ全て期待順序、exit 0（40秒、温cache）。cancel→即時停止（exit 130、process残留なし）→再構築で完走（31秒）を実機確認。最大無イベント間隔23.5秒はGUIの応答待ち閾値（20秒）を超えるが、これは実際のuvパッケージ導入中に発生する正常な挙動で、応答待ち表示の設計目的通り |
| 5 | ffmpeg: 検出/導入・GPLv3表示 | ✅ 検出branch（共有ffmpegを検出）を実確認。GPLv3表示は`setup.ps1`該当行を確認（実行はffmpeg既存のため導入branch自体は不通過） |
| 6 | モデル取得: サイズ・所要時間の事前表示・「後で」選択可 | ✅ 隔離cacheへ`faster-whisper:large-v3-turbo`を実dl。事前表示1.6 GiB、実サイズ1.6GiBで一致 |
| 7 | トークン: 同意必要性の明示・無効トークンと未同意の区別 | 実機では未検証（実credentialを使わない方針のため）。既存のfake-basedテストで区別ロジック自体は回帰済み（AISTATE.md Phase 5c記録） |
| 8 | 完了確認: 実際に文字起こしが動作する | ✅ 隔離venv・隔離モデルへ合成無音WAVで実`transcribe`実行、exit 0で完走 |
| 9 | 完了後: 通常画面へ遷移・2回目起動でウィザード非表示 | ✅ `complete()`はsmoke test成功前は`WizardNotReadyError`で拒否、成功後は`status().first_run`が`false`へ切替ることを確認 |

### 未検証の項目と理由

- **`cuda`profile**: 本機にNVIDIA GPUがないため検証不能（Phase 5c時点から変わらず）。
- **uv未導入からの自動導入（実PATH書き込み分岐）**: `Install-Uv`関数は実`%LOCALAPPDATA%\utteran\bin`
  と実ユーザーPATH環境変数へ書き込む設計であり、既存のuv導入環境を壊さずにこの分岐を実行する
  安全な方法（別ユーザーアカウントや仮想マシン以外）が確立できなかった。Phase 5cでも同じ理由で
  実機未検証とされており、今回もその判断を維持した。
- **Hugging Faceトークンの実検証（無効token/未同意の実際のエラー文言）**: 実credentialを検証に
  使わない方針のため、実機での目視確認はしていない。区別ロジック自体（`license`と`token`
  カテゴリ）はfakeベースの既存回帰testで検証済み。
- **ネイティブWebView上での実際の画面描画・クリック操作**: `docs/Phase5c_GUI_セットアップ
  ウィザード_手動確認手順書.md`に委譲。今回はheadlessなservice層の実行のみ。

### 検証環境の後始末

1. 隔離cloneの`.venvs`内で生成されたjobは無し（隔離modelを使ったsmoke testの出力は隔離
   一時ディレクトリのみで、`job.cleanup`により自動削除された）。
2. smoke testが共有job dir（`%LOCALAPPDATA%\utteran\utteran\Cache\jobs`）に1件生成した
   `job_id=9cd2739004d928fd`（`smoke.wav`）を`jobs clean --job-id --yes`で削除し、
   同dir内の他の実利用者jobには一切触れていないことを確認した。
3. 隔離clone本体（作業ディレクトリ）と隔離model cache（1.6 GiB）を削除した。
4. 削除後、実`.venvs`（`linux-ci`/`win-cpu`/`win-intel`/`win-vulkan`）・実model cache・
   `%LOCALAPPDATA%\utteran`配下のconfig/memory-calibrationが変更されていないことを確認した。
   本機には元々`utteran-gui`のsettings.jsonが存在しない（GUI未起動）ため、この点でも
   実環境への影響はない。

## 品質確認

- モデル不要test: `pytest -m "not requires_model"` 280 passed（Git Bash／cp932ロケール環境）。
- `ruff check` / `ruff format --check`: 合格。
- `mypy`: 55 source files合格（Windows上での実行含む。3-4節参照）。
- 受入試験ハーネス（`tools/acceptance/harness.py`、既定実行=非long・非destructive）:
  162件中151 pass・4 fail・7 skip（4347秒）。7 skipは全てCUDA不在の理由付き
  （本機にNVIDIA GPUなし、従来と同じ）。4 failは個別に再実行して切り分けた。

  | ID | 一括実行時の結果 | 個別再実行 | 判定 |
  |---|---|---|---|
  | G4-09 | fail（timeout系） | pass（17.4秒） | 一括実行時のリソース競合による一過性の失敗。回帰ではない |
  | G14-08 | fail | fail（想定通り） | G0〜G13の全群を通した実行ではないため、`postflight`の集計検査が「未実行群あり」で失敗するのは想定通り。今回はTask A/Bに関連するP系・一部G系のみを対象範囲としたため |
  | P4-11 | fail | fail（同一内容） | **Phase 5c時点のcommit（`bd37ce3`）でも同一内容で失敗することを確認済みの既存の不整合**（`models download`をID省略・非対話環境で実行すると実際はexit 2だが、testはexit 1を期待）。今回のTask A/Bの変更とは無関係、かつ対象範囲外のため未修正・記録のみ |
  | P10-A | fail（300秒timeout） | pass（200.3秒） | 一括実行時のリソース競合による一過性のtimeout。回帰ではない |

  結論として、**今回の変更（Task A/Bで修正した3件）による受入試験の新規回帰は確認されなかった。**
  P4-11は既存の不整合として記録するに留め、修正はTask A/Bの範囲外と判断した。

## 5dへ向けた申し送り事項

1. **uv package cacheが共有される環境での計測は温cache値である。** 5dのインストーラー化では
   真にネットワーク帯域律速となる初回導入の所要時間・体感を、cacheを持たない別マシンか
   仮想マシンで最低1回は実測することを推奨する。
2. **uv未導入からの自動導入（実PATH永続書き込み）は依然として実機未検証。** インストーラーが
   最初に遭遇する可能性が高い分岐であり、5dでは仮想マシンまたは新規ユーザーアカウントでの
   実機検証を優先することを推奨する。
3. **`verify`段のdevice診断table罫線文字の文字化け（3-2）は未解決のまま残っている。**
   インストーラーの完了画面やログ表示にこの詳細ログをそのまま転用しないよう注意する
   （現状は「詳細ログ」の折りたたみ内表示のみのため実害は小さいが、5dでインストーラーの
   完了画面へこのログを目立つ形で転用する場合は、装飾文字の文字化けが利用者の目に触れる
   可能性がある）。
4. **`Install-Uv`のダウンロード先（`%LOCALAPPDATA%\utteran\bin`）とユーザーPATH書き込みは、
   環境変数等でオーバーライドできない。** インストーラー文脈で導入先を変更したい場合
   （例: インストーラー自身のディレクトリ配下に閉じ込める等）は、`setup.ps1`側に
   オーバーライド手段を追加する設計変更が必要になる。
5. 今回の検証はGUIの`SetupWizardService`をservice層で直接駆動する方式で行った。実際の
   インストーラーはこのGUIプロセス自体の起動（`gui.ps1`相当）から始まるため、5dでは
   「インストーラー→初回GUI起動→ウィザード自動起動」という、GUIプロセス起動そのものを含む
   通しの実機確認が別途必要になる。
