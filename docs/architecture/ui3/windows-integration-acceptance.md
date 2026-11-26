# #88 / #91 / #93 Windows 統合受入（2026-11-26）

正本: [#22 統合指示](https://github.com/zawa356/Utteran/issues/22#issuecomment-7181173597)。
**総合判定は CONDITIONAL（未完了）。Windows native再出力は無効。GPU実機は未受入。**
Ready化、main merge、tag、Release、既定GUI切替、既存PRの変更・閉鎖は行わない。

## 対象と追跡方法

| 項目 | 固定値 |
|---|---|
| main | `f859a5d6345f4a824de5c43f6b8f1baa77688d78` |
| #88 | `14dfaecd392b6b55e04f4d9e3c410173e147e783` |
| #91 | `357dac29f5b676e8c429736abf5c1c64d521d559` |
| #93 | `127509d8fd3f65bba93fbd2ca52f325c27c5cd4f` |
| 統合 | `587484a1bee117e4e42d951c73104b3e8b0f5865`、tree `e3c81fff0ecd3c557df660d01092a4657c58856d` |
| 修正 | `e4b886a70487314a47d88d908583d9cd9f7c6161`、tree `3c53ff531e77367fbe94be5153eabfcb211cf5d9` |
| branch / version | `codex/windows-integration-acceptance` / **0.1.72（未リリース）** |

別cloneで#93から分岐し#88を通常merge。#91が#93の祖先であることを確認し、二重適用しなかった。
競合8ファイル（版/lock、日英README、AISTATE、変更履歴、architecture/ui3索引）を解消。
0.1.72を維持し、E-C2a/b・F・Gの記録と依存を併存させた。履歴の0.1.70欠番説明を整理した。
最終HEAD/tree、Full/Native/Light/Historicalのrun URLと結果は統合Draft PRに記録する。
本書以降の文書commitは実行した製品コードを変更しない。

## VMと安全境界

Windows 11 Pro / Hyper-V、build 26200（既存記録26200.9974）、論理CPU8。
物理推論GPUなし（Hyper-V Video / Remote Display）。実WebView2 **157.0.4341.60**、
VC++ x64 **14.52.37029.00 present**。RAMはHyper-V動的割当で約5.7～7.3 GiB、
開始時空きdisk約6.3 GiB。途中の割当・空き量を固定ハードウェア値と混同しない。
CPython **3.12.16**、uv **0.13.8**、Node **22.23.5**、npm **10.9.9**。
Rust **1.97.0**（native/内で選択を確認）・rustfmt/clippy、maturin **1.16.0**を専用領域へ導入。
MSVC **link.exeなし**、Inno Setup **ISCC.exeなし**。管理者への昇格・ホスト操作は行わない。

空の専用rootを製品のG-09検証でmarker化し、fixture/log/build領域を分離した。
公開モデル取得は専用HF領域、暗黙token無効、keyring nullで実施（個人資格情報を使用しない）。
入力は製品同梱のレビュー済み合成日本語WAVのみ。生ログ・パス・転写本文・配布バイナリは公開しない。
Defender/SAC/WDAC/SmartScreen、ACL、C:の8.3/LongPaths、ネットワーク設定を変更していない。
DPIも変更していない。VHD/share/mappingは作成していないため解除対象なし。
試験rootと再開用証跡をVM内に保持し、既存利用者root・旧試験資産を変更していない。

## Windows pytest停滞と修正

1. **固定#93で再現した退行**: 製品WAV上限試験の1 MiB超bytesが自動parametrize IDになり、
   Windowsの`PYTEST_CURRENT_TEST`が32767文字を超える。setup/teardownで`ValueError`、約6.3 MBの診断ログを生成。
   固定#93関連4ファイルは **206 passed / 2 skipped / 2 errors**、31.67秒。
   ローカルのPowerShell `Get-Content -Tail`でも巨大単一行の処理が停滞した。
   `ids=["corrupt", "oversized"]`のみ追加し、入力サイズ・拒否・書込みなしassertionは維持。
   同じ4ファイルは修正後 **207 passed / 2 skipped**。
   旧CI runのログは取得不能だったため、旧cancelled runの最終testを断定はしない。
2. **合成話者分離試験の実RAM依存**: 統合後全体試験で1件、実空きRAMによる`MemoryBudgetError`。
   合成backendの試験に十分な固定メモリ観測を注入し、製品のガードと既知不足拒否を維持。
3. **Windows合成Setup E2Eの停滞**: fixture終了監視がstdinを読んでいる間、合成Python子が同じpipeを継承。
   初回F試験は2成功/4タイムアウト。子に`stdin=DEVNULL`を指定後、F-01単独成功、F-01～06 **6/6成功**。
   timeout延長、skip、continue-on-error、必須gate削減は行っていない。

外側watchdogはunbuffered出力を時刻付きで逐次flushし、最後のtestを保持。
進捗停止210秒/全体900秒で専用pytest子treeの状態を採取して対象のみ終了する設計。
faulthandler timeout=120秒、終了codeをJSONに保存（Teeによる終了値消失なし）。watchdog発動なし。
統合初回全体は **1925 passed / 84 skipped / 1 xfailed / 1 failed**（301.43秒）、
修正後全体は **1930 passed / 83 skipped / 1 xfailed**（331.98秒）。
最終修正後の全体再実行と20/10反復はDraft PRの最終検証表に追記する。
skipはPOSIX固有・権限・git-filter-repo等の理由付きであり、VM必須受入のPASSには数えない。

## Setup・CPU縦断（実子プロセス、画面操作とは区別）

新規rootで製品`create_mode_app`、実B8 executor / setup.ps1 / locked profile / B4 / B7 / B3を使用。
API駆動はTestClientで行い、worker・モデル・推論・出力は合成runnerに置換していない。
専用ログ採取だけを追加し、公開するのは固定codeと集計のみ。

| ID | 結果 | 根拠・限界 |
|---|---|---|
| CPU-01 OS診断 | PASS | 物理表示adapterとruntimeを区別、VC++ present。診断中のSetupは409 `diagnostics_running`で拒否 |
| CPU-02 初回profile | PASS（修正後） | Store Python aliasのstderrでPowerShell 5.1が停止する不具合を修正。任意の版確認のみ警告化、locked sync必須 |
| CPU-03 子のPS環境 | PASS（修正後） | 継承PSModulePathでGet-FileHash読込不可を再現。子だけ既定検索へ戻し、新規profile構築成功。関連回帰24件成功 |
| CPU-04 最小モデル試行 | FAIL / 既知制約 | 公開tiny取得・manager検証は成功、B3/B4必須preprocessor_config不在で`smoke_model_incomplete`。条件を緩和せずturboへ。tiny/small等の導線は未解決として引継ぐ |
| CPU-05 B8→B4 | PASS | `faster-whisper / large-v3-turbo / cpu / int8`、公開モデル約1.62 GB、revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`。実smoke passed/evidence recorded |
| CPU-06 初回既定値 | PASS | Preset/Policy各1件をcreate-if-empty。画面からの標準SetupはBLOCKED、APIからのCPUカスタムSetupとしての結果 |
| CPU-07 B7安全拒否 | PASS | 再開後の動的メモリ減少時は`memory_insufficient`で投入拒否、メモリ条件を変更せず回復後に投入 |
| CPU-08 実worker | PASS | 単件1、batch2、取消後retry1の計4件succeeded。事前取消1件、強制worker killなし |
| CPU-09 出力・入力 | PASS | SRT/VTT/TXT/MD/JSON各4、永続completion evidenceの全5形式を照合。合成入力SHA-256不変 |
| CPU-10 復元・再検証 | PASS | 同一rootでruntime再起動、ID/状態/出力SHA-256、Preset/Policy各1件不変を照合。再検証の実smoke成功、defaultsはnot_applicable |
| CPU-11 Windows再出力 | PASS（拒否） | 必須VM受入未完了のためunsupported/no writes。5形式再出力成功を主張しない |

作業セット・用語集・取消/再実行の全画面経路、実CPU接続断、v6→v7移行killはNOT_RUN。
[#55](https://github.com/zawa356/Utteran/issues/55)を継続する。実CPU取消は**queued前提**でありrunning中取消の代替ではない。
gated community-1は明示提供・同意資産がないためNOT_RUN。実話者分離合格は主張しない。

## 実WebView2

専用rootで実起動、開発専用タイトル・Normalの初期UIA tree・v1待機キューを確認。
初回キャプチャは黒、再取得しても`GetCursorPos failed (0x80070005)`で入力拒否。
再開後にactivateは復旧したが、clickは`coordinate input geometry is unavailable`。
スクリーンショット・DPI・IME・picker・Explorer複数D&D・Normal/Expertの表示操作は**BLOCKED**。
CDP/Chromiumを実WebView2操作の合格に読み替えていない。OS倍率変更は実施していない。
既定legacyのAPI非退行とbundle/wheelは自動試験で確認し、既定GUIの実操作合格とは区別する。

## Rust安全FS W-01～W-23

正本: [handoff §3～5](e-c2a-windows-vm-handoff.md)。下表はこのVMの結果。
全行の対象コードは修正commit `e4b886a`。Rust fmt、Windows lib型検査・lib clippyはPASS。
all-targets clippy / cargo test / maturin wheelは**link.exe不足でBLOCKED**。
以前のrunner結果をこのVMのPASSに流用しない。

| ID | 必須 | 結果 | 根拠・未実施理由 |
|---|---|---|---|
| W-01 NTFS | ○ | BLOCKED | MSVC/wheel不足、実core未実行 |
| W-02 保持祖先 | ○ | BLOCKED | 同上 |
| W-03 DELETE handle | ○ | BLOCKED | 同上 |
| W-04 別process/Explorer | ○ | BLOCKED | wheel不足、native入力操作不可 |
| W-05 writer排他 | ○ | BLOCKED | MSVC/wheel不足 |
| W-06 junction | ○ | BLOCKED | 同上 |
| W-07 symlink | ○ | BLOCKED | 権限不足skipの観測あり。権限昇格しない |
| W-08 8.3 | 任意 | NOT_RUN | 専用VHD未作成。C:設定変更なし |
| W-09 Unicode/長パス | ○ | BLOCKED | 実core未実行 |
| W-10 UNC/device | ○ | BLOCKED | 同上 |
| W-11 SUBST/shadow | ○ | BLOCKED | 同名shadow用の専用VHD未作成。通常/global identity照合をopen_rootへ追加（verify_reachableも同経路）。実shadow競合は未検証 |
| W-12 SMB | ○ | BLOCKED | MSVC/wheel不足、一時share未作成 |
| W-13 FAT32/exFAT | ○ | BLOCKED | MSVC/wheel不足、専用VHD未作成。ReFSもNOT_RUN |
| W-14 reparse競合 | ○ | BLOCKED | MSVC/wheel不足、別process/barrier未実装・未実行。target/元ジョブ不変の実証なし |
| W-15 kill/handle解放 | ○ | BLOCKED | 実core未実行 |
| W-16 ACL継承 | 任意 | NOT_RUN | native再出力未実行 |
| W-17 権限拒否 | ○ | BLOCKED | 実core未実行、ACL変更なし |
| W-18 OneDrive | 任意 | NOT_RUN | 専用placeholder試験なし |
| W-19 | — | 対象なし | 正本に項目定義なし |
| W-20 Rustなしwheel | ○ | BLOCKED | wheel生成はMSVC不足で失敗 |
| W-21 SAC/WDAC | 任意 | NOT_RUN | 保護機構を変更しない |
| W-22 製品層 | ○ | BLOCKED / 拒否回帰PASS | native wheelなし。missing/broken/capableの3条件でHTTP unsupported/no writesを確認、native CIにも追加 |
| W-23 配布 | ○ | BLOCKED | 実build.ps1はInno Setupなしで失敗。native wheelも未生成。完成installer/portableなし |

**必須が1件でもPASSでなければWindows製品経路は無効。** この候補ではwheelがあっても`supported()`はFalse。
既存native adapter試験はテスト内だけ明示的に有効化して継続し、一律skipしない。
Rust core / POSIX製品回帰は維持。無効化を解除する環境変数・設定・自動fallbackは追加しない。
marker/DB claim非原子性、孤立部分dir、ACL継承のみ、dir flushなし、Git判定の時点観測は残留。
安全FSの実受入前にWindows再出力を解禁してはならない。

## 品質・配布・引継ぎ

ローカル: ruff/format、mypy（native / win32）、lock、8 schema、PowerShell BOM、
frontend lint/typecheck/Vitest **682**、frontend build、隔離製品wheel probeはPASS。
wheel probeは`tests/`なしで同梱WAV 826446 bytesをdigest確認し、bundle2資産・nonce・legacy既定を確認。
これはinstaller/portableの完成物試験ではない。成果物バイナリは公開していない。
privacy worktree scanはblocking **0**。最終commitのdelta、反復、全体pytest、CIはDraft PRで確定値を報告する。
独立reviewerは使用せず自己レビュー（正本との照合、diff、拒否回帰、既存gate維持）を実施。

Intel実機は[g-multiprofile総合受入](g-multiprofile.md#総合受入22)を引継ぐ。
Vulkan encoder/decoder、OpenVINO encoderとdecoder別記録、XPU/CPU話者分離、tokenなしoffline、
長尺/OOM/取消再開、driver/variant変更時の証拠無効化、実全形式出力を確認する。
CUDAのcold/refreshと実推論は[#89](https://github.com/zawa356/Utteran/issues/89)を継続。
[#55](https://github.com/zawa356/Utteran/issues/55)、[#15](https://github.com/zawa356/Utteran/issues/15)、署名[#7](https://github.com/zawa356/Utteran/issues/7)、正式配布[#8](https://github.com/zawa356/Utteran/issues/8)も未解決のまま。
VMのCPU結果でIntel XPU/Vulkan/OpenVINO/CUDA GPU受入を完了扱いしない。

再開時は統合Draft PRの最終SHAをcheckoutし、VM内の専用証跡の終了JSON・試験ID・CPU summaryを確認。
MSVC/Windows SDK/Innoと安定した対話desktopが用意できる環境でW表・画面項目を続行する。
既存rootを初期化せず、GPU実機・Windows nativeの未受入をPASSへ読み替えない。
