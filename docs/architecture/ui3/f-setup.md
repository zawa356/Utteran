# 束F — 新GUIセットアップ・環境診断・ハードウェア検出（0.1.71）

更新: 2026-11-25 JST。開発版 **0.1.71（未リリース）**。基点 main `f859a5d`（0.1.69）。指示 [#90](https://github.com/zawa356/Utteran/issues/90)、関連 [#89](https://github.com/zawa356/Utteran/issues/89)。
PR #88（0.1.70、Windows VM 受入待ち）とは独立。対象は `--next-ui-v1`（開発専用、マーカー付き専用データ root）だけで、既定 GUI・CLI・旧 `--next-ui`・旧ウィザードは変更しない。

> Cloud（Linux）での合成試験の記録。実 GPU・実モデル・Windows 実機・実 VC++ 診断の成功はどこでも主張しない。実機確認は §8 の手順で [#22](https://github.com/zawa356/Utteran/issues/22) に引き継ぐ。

## 1. 初回セットアップから実行までの経路（実装後）

```text
[セットアップ]ページ（Normal/Expert 共通）
 1. GET  /api/v1/setup-diagnostics            表示時は読むだけ（probe を起動しない）
    POST /api/v1/setup-diagnostics/refresh    ［環境を確認］［再検出］。202 即応答、背景で single-flight
 2. 標準（推奨 profile + large-v3-turbo）／カスタム（profile・複数モデル・主モデル・話者分離）
 3. POST /api/v1/setup-plans                   read-only。verification_settings = 共通既定値（主モデル、ASR のみ）
 4. 計画の確認（変更・容量（不明は不明）・手順・同意・資格情報） → ［この内容で開始］
    POST .../approve → POST /api/v1/setup-operations
 5. キューが一時停止中なら［キューの実行を再開］（起動時は自動再開しない既存仕様）
 6. B8 executor（B7 と同じ dispatcher・heavy slot・fence・journal）
    venv_sync → credential_preflight → model_download → [smoke（話者分離時のみ）] → capability_smoke
 7. 成功後: POST /api/v1/setup-operations/{id}/defaults（plan の apply_defaults、B5 で 1 transaction の
    create-if-empty、plan id ごとに 1 回だけ記録）→ created なら選択、kept なら何もしない
 8. POST /api/v1/readiness-checks              選択中の Preset/Policy で B7 が受け付けるか（推論なし）
 9. 通常の B7 経路（execution-resolutions → jobs）
```

- 初回と追加／修復の区別（PR #91 P2-1 で server 側へ移した）: 標準/カスタムの plan は `apply_defaults: true` を hash 対象の plan に保存し、再検証の plan は持たない。成功した operation について server が B5 の 1 transaction で「Preset も Policy も無いときだけ両方作成、あれば何もしない（kept）」を行い、その結果を plan id のキーで記録する。片方だけの作成、二重作成、既存資産の変更は起きない。応答喪失・再読込・別画面からの再要求は同じ記録を返す。画面を閉じている間に成功した場合は、次に画面を開いたとき `GET .../defaults` が `pending` のものだけ POST する（表示時の書込みはその場合だけ）。失敗時は［既定値の作成を再試行］（E2E F-01/F-03/F-06、Vitest、pytest）。DB layout の変更なし（plan 本文の任意 field と B5 replay 行のみ）。
- Setup 成功と実行 ready は別表示。ready は B4 evaluator が証拠を読んで判定したときだけ（`fs.ready.*`）。
- 再起動・再読込: 画面は `GET /setup-operations` から最新の未完了 operation を復元し、`GET /setup-operations/{id}` を読むだけで追従する（E2E F-04）。
- 再検証: readiness が `smoke_missing`（未検証・期限切れ・設定変更）のとき［この設定で検証を実行］で、選択中 Preset の設定そのものを検証する plan を作る（同じ確認→開始）。話者分離付き・faster-whisper 以外・worker が読めないモデルは理由付きで無効。

## 2. B8 → B4 証拠（F-A の中心）

| 項目 | 実装 |
|---|---|
| 契約 | `SetupPlanRequest.verification_settings`（任意）。`PlanContent.verification` に recipe（cpu→`fw-cpu`、cuda→`fw-cuda`）・モデル・設定を保存。**plan の公開 view は設定の digest だけ**（installation 秘密鍵の HMAC-SHA256。短い prompt を推測照合できないようにした。prompt 等は返さない）。指定なしの plan は従来と同じ形・hash |
| 拒否 | intel/vulkan、faster-whisper 以外、話者分離有効、plan に無いモデル → 422 `verification_unsupported` |
| ステップ | 新 kind `capability_smoke`（`verify`）。既存 B4 smoke 子 `python -I -B -m utteran.capabilities.smoke run` を **B8 の `_child`（intent→PID→exit の journal、heavy slot、fence、協調取消）**で起動。私的 request は stdin のみ、環境は B4 bridge と同じ allowlist（fingerprint を同一に計算するため） |
| 結果 | 子の固定 outcome 1 行だけを読む。`passed` → `succeeded`（evidence `capability_evidence_recorded`）。それ以外は `smoke_failed` と分類済み理由（`smoke_backend_unavailable` 等）。生コード・出力は保存しない |
| 証拠 | 子が既存どおり `<root>/cache/capability-evidence-v1/` に書く（G-09 マーカー必須、TTL 24h、scope = identity + settings 全体）。B4 refresh/resolve と B7 admission/prestart は従来どおり読むだけ |
| 境界 | smoke を起動できるのは開発用入口と承認済み B8 ステップだけ（`test_smoke_is_reachable_only_from_the_explicit_entry` を更新）。GET・resolve・GUI 起動は推論しない。二重ロックの解除なし（GUI プロセス内の heavy slot で実行） |
| 統合回帰 | `test_cpu_setup_verification_makes_the_same_settings_ready_and_admittable`: 実 B8 executor → 実 `run_smoke`（fake runtime）→ 実 B4 read-back → readiness `not_ready(verifiable)` → `ready` → 実 B7 受理・prestart。言語を変えると別 scope で not_ready |

`POST /api/v1/readiness-checks`（`setup_readiness.py`）は B7 の provider（bridge resolve、metadata のみ）を 1 回呼び、固定コードと catalog 識別子だけを返す。トークン発行・証拠変更なし。provider は未選択時の選択結果を `unselected` として保持するようにした（admission の判定は不変）。

## 3. CUDA の証拠（F-B / #89 の調査結果と最小実装）

| 証拠 | 取得元（新規 `utteran/capabilities/nvml.py`、ctypes・読取のみ） | 取れない場合 |
|---|---|---|
| runtime fingerprint (fw-cuda) | パッケージ版＋CUDA wheel 版＋NVML driver 版・CUDA driver API 版＋`CUDA_VISIBLE_DEVICES`/`CUDA_DEVICE_ORDER` | `None` → unknown |
| hardware fingerprint (fw-cuda) | **NVIDIA GPU が 1 台かつ `CUDA_VISIBLE_DEVICES`/`CUDA_DEVICE_ORDER` 未設定のときだけ**、その GPU の UUID・名前・総メモリ（ordinal 0 = その GPU） | 複数 GPU・変数設定・NVML 不可 → `None` → unknown |
| ordinal ↔ 安定 identity | smoke 子の中で CUDA driver API `cuDeviceGetUuid(0)` がその 1 台の UUID と一致した場合だけ VRAM を記録 | 不一致・ordinal≠0 → VRAM unknown |
| available VRAM | smoke 直前の NVML free（その UUID） | unknown |
| required VRAM | load〜推論〜解放直前を 100 ms 間隔（最大 6000 回）で NVML per-process 使用量を標本化した最大値。**短尺 fixture の下限値**で長時間音声の保証ではない | 標本なし（WDDM 等で N/A）→ unknown |

範囲の限定（PR #91 P2-2）: 以前は GPU 集合の hash だけで束縛しており、同じ集合のまま ordinal が別 GPU に変わると過去の証拠が再採用され得た。今回は単一 GPU に限定し UUID に束縛したため、GPU の交換・追加・可視性変数の設定で scope が変わるか unknown になる。**多 GPU 機の fw-cuda は当面 ready にならない**（後続で、証拠の ordinal↔UUID を refresh/prestart で再照合する設計が必要）。Windows WDDM では per-process 使用量が N/A になり得るため、fw-cuda が ready になるかは実機でしか分からない。合成試験の成功を GPU 成功とは扱わない。

## 4. 検出の安定化（#89）

- 原因の切り分け（コード調査）: 初回の重い処理は CTranslate2 の CUDA 列挙（`get_cuda_device_count`）・`get_supported_compute_types`・torch CUDA の初回 import（cuBLAS/cuDNN/torch DLL のコールド読込）。各 probe は 20 秒で打ち切られ、timeout は cache されないため 2 回目（OS のファイル cache が温まった後）は成功し得る。これは仮説で、NVIDIA 実機の実ログでは未確認。
- `devices.py`: `ctranslate2_cuda_count`/`ctranslate2_cuda`/`torch_cuda` の **timeout だけを 1 回再試行**（**1 pass で 1 回まで、pass 開始から 70 秒以内に終わる場合だけ**、`ProbeOutcome.attempts`）。エラー・確定値は再試行しない。timeout は従来どおり cache しない・unknown のまま。**このモジュールは旧 CLI `utteran devices` と旧 `/api/environment`（外側 200 秒）にも共通**で、両者もこの再試行を行う。1 GPU で全 probe が timeout する最悪は 9 probe×20 秒＋再試行 20 秒＝200 秒で、旧 `/api/environment` の外側 200 秒に達し得る（その場合は従来どおり失敗表示で、GPU なし扱いにはしない）。典型の「最初の CUDA probe だけ遅い」場合は十分内側に収まる（P3）。
- `nvidia-smi` の index と CUDA ordinal を同一視していた表示を廃止。CUDA 1 台・NVIDIA 1 台の一意な場合だけ名前・メモリを対応させる。
- GUI 側 `setup_diagnostics.py`: OS 段（CIM の GPU・driver 版、メモリ、ディスク、VC++）→ runtime 段（**cuda profile が存在し互換の場合だけ** `devices --json`。CPU 用 venv の「CUDA なし」は GPU の可否として扱わず、NVIDIA を OS が報告していれば推奨は cuda（未確認）のまま。P2-4）。新しい runtime pass の開始時に前回の結果を消す。外側の再実行は CLI プロセス自体の timeout のときだけ（1 回、各 300 秒、総 660 秒）で、payload 内の probe timeout は devices.py が既に再試行しているので再実行しない。最初の試行の状態・秒数を `first_attempt` に残す。背景 1 スレッドの single-flight（並行要求は合流）、世代番号で古い結果の上書きを防止、setup 実行中は probe しない、timeout は「GPU なし」「CPU 確定」にしない。
- 実行中に来た強制再検出は捨てず、現在の pass の後に 1 回だけ実行する（`rerun_pending`）。CLI の timeout は adapter が `CliTimeout`（`CliError` の派生）に変換するため、それを捕捉して再試行する。再試行の判定は CUDA 系 probe の timeout だけ。
- 排他（PR #91 P2-3）: プロセス内の共有 `ExecutionGate` を、B7/B8 dispatcher が 1 回の dispatch（job または setup、子 process を含む）の間、runtime probe がその全体の間だけ保持する。双方とも try-acquire なので確認と開始が不可分で、probe 中に queue の setup/job は dispatch されず（次の周回で再試行）、job/setup の実行中に probe は始まらない（`execution_running`）。queue pause 中に登録→probe 開始→dispatch 再開の順でも並行しない。API の 409 `diagnostics_running` は早期の分かりやすい拒否として残すが、保証は gate による。B7 ジョブ実行中の重い probe もこれで保留される。逆に probe の実行中（最悪約 660 秒）は待機中の文字起こしジョブも dispatch されない（設計上の帰結）。dispatcher の lease が残っている場合（子の終了未確認・recovery_required）も probe しない。
- 旧 `/api/wizard/hardware` と `/api/environment` は変更しない（旧 GUI 維持）。重複 probe の解消は新 GUI の診断経路のみ。

## 5. VC++・ドライバーの診断と修復導線

- VC++ 2015-2022 x64: レジストリ `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64`（`Installed`/`Version`、64bit view）**と** System32 の `vcruntime140.dll`/`vcruntime140_1.dll`/`msvcp140.dll` の存在。両方 → present、どちらも無い → missing、片方 → partial。DLL が読み込めることは保証しない。不足時は公式 URL を表示するだけで導入しない。
- NVIDIA: OS が NVIDIA アダプターを報告し driver 版が無い → `nvidia_driver_unknown` と公式配布先を表示。ドライバー・CUDA・VC++ の自動導入はしない。
- 修復: 既存の B8 retry/resume、モデル追加・検証（verify-first で再利用）を使う。既存 venv の再構築は従来どおり `profile_rebuild_unsupported`（理由と「新しいデータ領域でのクリーン setup」を案内。既存データは消さない）。アプリ初期化・キャッシュ削除・成果物削除は対象外。

## 6. 話者分離（F-A / 0.2 の試用目標に対する現状）

- 今回: setup で pyannote の導入・トークン有無・同意（承認時の agreement id）・CLI smoke による動作確認を行う。UI はトークン状態と同意を表示し、不足時は開始不可（E2E F-05）。
- 未対応（後続 Issue を提案）: B3 worker は `diarization=False` 宣言で話者分離付きジョブを拒否し、B4 smoke は ASR のみ。CUDA＋話者分離を v1 で実行するには、(1) pyannote の offline ロード（遠隔 component 解決の封止）の証明、(2) diarization device/identity の B4 証拠（torch CUDA）、(3) worker の token 受け渡しと engine 宣言の解禁、(4) 実 GPU 受入が必要。広範な worker/契約変更と検証不能な GPU 解禁になるため今回の必須範囲から外した。

## 7. 0.2 移行の注意（F-C）

- 0.1 系の設定・履歴・DB の互換移行は実装しない（オーナー決定）。新 GUI はマーカー付きの新しいデータ root でのクリーン setup を前提にする。既存の利用者データは読まず、消さず、上書きしない。
- main 内で必要な migration（control store v1→v8 の一方向）は従来どおり。今回 DB layout の変更はない（plan 本文に任意フィールドを追加しただけ）。
- 既存の profile が現行 lock と不一致なら `profile_rebuild_unsupported` で止まる。その場合は別の新しいデータ root でクリーン setup する。モデルは verify-first で同じ root 内の完全なコピーだけ再利用する。
- 既定 GUI の切替・旧経路の削除は UI-4 以降。

## 8. Windows 実機受入手順（#22 へ引継ぎ、未実施）

前提: マーカー付き専用データ root（既存データの無い空フォルダ）。`uv run --no-sync utteran-gui --next-ui-v1`。

| # | 手順 | 期待 |
|---|---|---|
| W-F1 | ［セットアップ］→［環境を確認］ | GPU 名・driver 版・VC++ が表示される。表示までに GUI が固まらない |
| W-F2 | VC++ を一時的に外した VM（または未導入 VM）で同上 | missing/partial と公式 URL。導入はされない |
| W-F3 | NVIDIA 機・cold boot 直後に［環境を確認］→（profile 作成後）［再検出］を 3 回 | 各回の CT2/torch の値・`attempts`・所要時間を記録。timeout が「GPU なし」と表示されない |
| W-F4 | 標準（CUDA 推奨）で計画→開始→［キューの実行を再開］ | venv 作成・`large-v3-turbo` 取得・検証が順に進む。所要時間・容量を記録 |
| W-F5 | 完了後の「実行の可否」 | fw-cuda が ready か unknown か（`memory_unknown`/`evidence_scope_incomplete` の有無）をそのまま記録。CPU への自動切替が無いこと |
| W-F6 | CPU 機で同じ流れ | fw-cpu が ready、通常の文字起こしが開始できる |
| W-F7 | ダウンロード中に GUI を終了→再起動 | interrupted/recovery の表示、［再開］で verify-first により再取得しない |
| W-F8 | 話者分離をオン（トークン・同意あり／なし） | なし: 開始不可。あり: 導入と動作確認まで完了。v1 キューでは話者分離付きジョブは拒否される |
| W-F9 | 証拠ファイルの確認 | `<root>\cache\capability-evidence-v1\*.json` にパス・prompt・文字起こし・トークンが無い |

## 9a. オーナーレビュー（PR #91、2026-11-26）への対応

| 指摘 | 対応 |
|---|---|
| P2-1 既定値作成の復旧 | §1。server 側の原子的 create-if-empty（B5 1 transaction）＋ plan に永続化した `apply_defaults`、plan id キーの記録・再照合、閉じている間の成功を次回表示で完了、失敗時の再試行 |
| P2-2 CUDA 証拠の束縛 | §3。単一 GPU・可視性変数なしに限定して UUID に束縛、smoke の ordinal 0 attestation、VRAM は有界標本化の最大値（下限値）、測定不能は unknown |
| P2-3 診断と setup の競合 | §4。共有 ExecutionGate（dispatcher と probe の try-acquire）、固定 barrier で両方向の交差順序と例外時の解放を回帰 |
| P2-4 CPU profile の外挿 | §4。cuda profile のみで判定、不在・不互換は理由付き unknown、新 pass で古い結果を消す |
| P3 retry の重なり | §4。devices.py は 1 pass 1 回・100 秒窓、診断は process timeout のみ再実行、旧 CLI/API への影響を明記、`first_attempt` |
| P3 readiness の古い応答 | 応答に Preset/Policy の revision キーと世代を持たせ、選択変更・新しい確認の後に届いた応答は表示しない |
| P3 CUDA＋話者分離 | 後続 Issue に分離（PR 本文参照）。#89 は実 GPU の cold/refresh 確認が残るため未解決のまま |
| P3 main 統合 | #88 統合後に最新 main を取り込み、版・lock・AISTATE・変更履歴の競合を整理し両機能を維持する（今回は未実施） |

## 9. 独立レビューへの対応（2026-11-25）

サブエージェントによるレビューで重要 5 件・軽微 5 件。全て同じ作業単位で修正し回帰試験を追加した: 実 CLI の timeout で再試行が働かない（`CliTimeout`）、検出完了で確認済みの計画と異なる profile が表示される（手動選択・計画後は推奨で上書きしない）、再読込後の初回成功で既定値が作られない（plan の検証モデルから復元）、probe 中に setup が始まる（409）、強制再検出が合流で失われる（保留して実行）、未確認の子の一時ファイル削除、digest の鍵付け、CUDA 以外の timeout で再試行、無効になった承認の再利用、不明結果の再送時の設定不一致。

## 10. 試験（Cloud Linux、合成のみ）

- pytest: `tests/test_f_setup.py`（plan/検証の拒否、executor の capability_smoke 成功・失敗分類・retry、未構築→ready→B7 統合、readiness、診断の初回 timeout→再試行・重複要求の合流・古い結果・unknown、VC++、#89 再試行と番号対応、NVML/fingerprint、HTTP）。既存 B8/B4 試験は維持（境界試験の許可先に B8 ステップを明示追加）。
- Vitest: `src/domain/v1/setup.test.ts`（表示時は GET のみ、計画内容、主モデル順・話者分離・同意、資格情報不足、既定値の一度だけの作成と既存設定の維持、キュー再開、retry/不明時の再読込、復元、再検証の可否）。
- Playwright: `e2e/setup-f.v1.spec.ts` F-01〜F-05（実 API＋合成 runner、`serve_next_ui.py --setup-e2e`）。
- 件数・CI・HEAD は Draft PR に記録する。
