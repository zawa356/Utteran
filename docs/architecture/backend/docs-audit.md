# Docs audit — Backend Foundation Hardening (2026-11-03)

方針: 過去の監査・検証の事実は書き換えない。現行と食い違う箇所は冒頭/末尾の**追補**と正本へのリンクで誘導する。
重複した仕様の再掲はしない。

| 文書 | 判定 | 理由・変更内容 |
|---|---|---|
| `AGENTS.md` | **new** | 既存の`AGENTS.md`/`CLAUDE.md`/`.claude/`は無かった。読む順・実行境界・品質ゲート・privacyのみ |
| `docs/architecture/README.md` | **new** | 現行正本と歴史資料の区別、優先順位 |
| `docs/architecture/backend/*.md` | **new** | README・components・interfaces・persistence-recovery・testing-and-operations・validation・本書 |
| `README.md` / `README.en.md` | updated | B8/B9見出しの「Draft・未マージ」→「main統合済み・未リリース」。B6節の「Setup/SSE未提供」→「B6単独では未提供、B8/B9で追加（opt-in）」。版0.1.39、正本へのリンク。日英で同内容 |
| `AISTATE.md` | updated | 冒頭に現在地を追加。以前のDraft記録は改変なし |
| `変更履歴.md` | updated | 0.1.39節 |
| `要件定義.md` | updated | B9節にmain統合済みの1行追補のみ |
| `docs/architecture/integration-2026-11-02.md` | updated | B7 PR #32 / B8+B9 PR #33のmerge commit、Pages公開成功済み、#27是正への追補 |
| `docs/architecture/b8/README.md` | updated | 冒頭に「Draft提出時の記録、現在はmain統合済み」 |
| `docs/architecture/b8/storage.md` | updated | **訂正追補**: pre-v1 backupは0.1.38まで実際にはv6のコピーだった（コード修正済み） |
| `docs/architecture/b9/README.md` | updated | 冒頭に統合済み・**現行=v6 `ManagedControlStore`、v5=`EventsControlStore`**の追補。本文表の旧記述は履歴として残す |
| `src/utteran_control/events_repository.py`, `src/utteran_gui/job_runtime.py`（docstring/comment） | updated | 「ManagedControlStore=v5」の古い記述をv6へ |
| `docs/UI_UX_刷新計画.md` | updated | 冒頭に2026-11-03現況（B7〜B9統合済み、U0〜U3未着手）1段落 |
| `docs/architecture/phase-a/migration.md` | updated | 同上の現況1段落 |
| `docs/architecture/phase-a/README.md`, `openapi.yaml` | reviewed-no-change | 提案時の歴史資料として保持。差異は[interfaces.md](interfaces.md#phase-a-openapiとの差異)に集約 |
| `docs/architecture/b1`〜`b7/*` | reviewed-no-change | 提出時の記録。B7は既に統合済み追補あり。現在地は[README.md](README.md) |
| `docs/architecture/b9/{events-sse,sync,retention,diagnostics-legacy,validation}.md` | reviewed-no-change | 仕様は現行と一致（v6はretention.mdに記載済み） |
| `docs/リリース手順.md` | reviewed-no-change | patch versionを作業単位で上げる規則に従い0.1.39とした。手順変更なし |
| `.github/workflows/ci.yml` | updated | 独立job化（[testing-and-operations.md](testing-and-operations.md)） |
| `tools/public_history_scan.py` / `tools/public_scan_reviewed_media.json` | updated / new | `.ts`内容判定、レビュー済み`(path, blob)`、diff記号の誤検出修正 |
| `tools/acceptance/*` | reviewed-no-change | 旧CLI経路の実機受入用。v1の根拠ではないことを[testing-and-operations.md](testing-and-operations.md#既存のtoolsacceptance)に明記 |

## PR #34レビュー修正での追補（2026-11-03）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `tools/public_history_delta.py` / `tools/public_history_baseline.json` / `tests/test_public_history_delta.py` | **new** | P2: 新規commit全系列のprivacy gate、固定cutover、17件の回帰テスト |
| `.github/workflows/ci.yml` | updated | `privacy-history-delta` jobを追加（12 job）。他jobは不変 |
| `testing-and-operations.md` | updated | 3つのprivacy検査の役割分担、範囲・cutover・将来PRの基準、fail-closedコード、信頼の限界（branch protection / required check / CODEOWNERS） |
| `validation.md` | updated | P3: 68（`b83669c`）と69（`51dc90f`）を別時点の実測として記録し、差の1件を特定。P2の結果。過去の表は注記の追加のみ |
| `AGENTS.md` | updated | 品質ゲートに新規履歴gateを追加 |
| `変更履歴.md` | updated | 0.1.39のCI節に新規履歴gateを1行追加（versionは0.1.39のまま） |
| `README.md` / `README.en.md` / `AISTATE.md` / `要件定義.md` / Phase A・B8・B9文書 | reviewed-no-change | 利用者向け機能・API・version（0.1.39）・DBに変更はない。CI/privacyの正本はbackend文書に集約している |

## PR #34追加レビュー修正（placeholder判定、2026-11-03）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `tools/public_history_scan.py` / `tools/public_history_delta.py` | updated | `_is_placeholder(detector, value)`をdetector別の値全体判定にした。delta gateも同じ関数を使う |
| `tests/test_public_history_scan.py` / `tests/test_public_history_delta.py` | updated | T01〜T11等12件を追加 |
| `tests/test_b6_control.py`、`test_models.py`、`test_private_history_match.py`、`test_acceptance_scenarios.py`、`tools/acceptance/scenarios.py` | updated | 合成値を実行時に組み立てる方式に変えた（値は不変） |
| `AISTATE.md` | updated | 実在のユーザーパス1件を伏せ字にした（現行treeのみ。過去の記録の他の記述は不変） |
| `ユーザー確認事項.md` | updated | `<名前>`の後ろにpath区切りを1文字追加 |
| `testing-and-operations.md` / `validation.md` / `AGENTS.md` / `変更履歴.md` | updated | placeholder基準、計測結果、fixture規則、0.1.39への追補 |
| README日英・要件定義・Phase A/B8/B9文書 | reviewed-no-change | 利用者向け機能・API・version・DBに変更はない |

## 公開Git履歴の書換え（2026-11-03）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| 全branch・tagの`AISTATE.md`過去版 | rewritten | 実在の個人パスのliteralだけを`C:\Users\<user>\`に置換（git-filter-repo） |
| `tools/public_history_baseline.json` / `tests/test_public_history_delta.py` | updated | cutoverを書換え後SHA `e0960bd`へ変更 |
| `testing-and-operations.md` / `validation.md` / `変更履歴.md` / `AISTATE.md` | updated | 新しいcutover、SHA対応表、旧SHAが書換え前の記録であることの注記 |

## G-09 開発専用`--next-ui-v1`起動・GUIプロセス排他（2026-11-11、0.1.49）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `docs/architecture/ui3/g09-next-ui-v1-startup.md` | **new** | 3モード、開発用root条件とmarker、GUIロックの方式と保護範囲、ポート先取り、停止順序・失敗時、試験、後続ゲート |
| `docs/architecture/ui2/migration-next.md` | updated | 冒頭の最新方針にG-09実装状況・3モード・暫定フラグの廃止条件・後続ゲート。G-09行。旧計画は履歴として保持 |
| `docs/architecture/ui1/backend-gap-matrix.md` | updated | G-09を「一部解消」とし、残る制約（CLI・旧バイナリ・非協調プロセス、v1投入接続、owner判断）を明記 |
| `docs/architecture/backend/persistence-recovery.md` / `interfaces.md` | updated | 同一プロセス排他とGUIプロセス間排他の区別、開発用root、shutdown結果code、GUI起動モードと有効化引数 |
| `docs/architecture/b7/compatibility.md` | updated | `app.py`のv1有効化は開発専用フラグに限る旨、GUIプロセス排他の追補（当時の本文は保持） |
| `docs/architecture/README.md` / `ui3/README.md` / `AISTATE.md` / `変更履歴.md` / `README.md` / `README.en.md` / `AGENTS.md` | updated | 0.1.49開発版・未リリース・v1製品接続は未完了、実行境界に`--next-ui-v1`を追記 |
| `docs/architecture/ui3/design-freeze.md` / `s3a-legacy-transcription.md` / `s3b1-multi-input-and-admission.md` | reviewed-no-change | 画面設計・旧API投入の仕様は不変（`--next-ui-v1`で旧投入が409になる点はg09に集約） |
| `docs/architecture/b9/sync.md` / `backend/README.md` | reviewed-no-change | reducer・同期仕様、B5〜B9の提供状況は不変（v1は引き続き明示opt-in） |
| `docs/リリース手順.md` | reviewed-no-change | 規則どおりpatchを0.1.49へ（`pyproject.toml`・`uv.lock`。`__version__`はmetadata由来）。手順変更なし |

## B4 ready/smoke 明示実行・private evidence（2026-11-12、0.1.50）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `docs/architecture/b4/smoke.md` | **new** | Gap調査（CTranslate2が観測できるroute・identity・memory）、責務境界、evidence形式v1・採用条件・失効・再smoke、実行制御、限界、Cloud確認範囲 |
| `docs/architecture/b4/smoke-local-acceptance.md` | **new** | Windows実機の受入手順（**未実施**）。専用root作成、profile/モデル準備、settings、smoke、ready確認、CUDA、後片付け |
| `docs/architecture/b4/README.md` / `observations.md` | updated | 0.1.50追補。標準collectorの制約は不変で、`local_provider`だけが証拠を読取専用で重ねる |
| `docs/architecture/backend/README.md` / `docs/architecture/README.md` / `ui2/migration-next.md` | updated | B4明示smoke行、B7実行の条件、次の工程（Windows受入→新UIのv1投入接続） |
| `AISTATE.md` / `変更履歴.md` / `README.md` / `README.en.md` | updated | 0.1.50開発版・未リリース、開発専用入口、実機未確認 |
| `docs/architecture/b4/capabilities.schema.json` ほか7 schema | reviewed-no-change | DTO・wire形式を変えていない（`--check`成功） |
| `docs/architecture/b3/*` / `b7/*` / `ui3/g09-next-ui-v1-startup.md` | reviewed-no-change | worker protocol・B7 admission/prestart・G-09 marker/ロックの仕様は不変（marker定数は値を変えず`utteran_paths`へ移設） |
| `docs/リリース手順.md` | reviewed-no-change | 規則どおりpatchを0.1.50へ（`pyproject.toml`・`uv.lock`）。手順変更なし |

## UI-3 S5-A 新UIからv1単一ジョブ（2026-11-12、0.1.51）

| 文書・ファイル | 判定 | 理由・変更内容 |
|---|---|---|
| `docs/architecture/ui3/s5a-v1-single-job.md` | **new** | 差分調査表、mode分離、Preset/Policy明示作成、二段階送信・凍結・Idempotency、paused/CAS、取消、unsupported、互換性、試験、CIとWindows未受入の区別 |
| `docs/architecture/README.md` / `ui3/README.md` / `ui2/migration-next.md` / `backend/README.md` | updated | 0.1.51の現況、S5段階表、B5 store初回並行openの修正 |
| `docs/architecture/ui3/g09-next-ui-v1-startup.md` | updated | 「旧投入409で開始できない」は当時の記録である旨を冒頭に追補（本文は保持） |
| `AISTATE.md` / `変更履歴.md` / `README.md` / `README.en.md` | updated | 0.1.51開発版・未リリース、`--next-ui-v1`の単一ジョブ経路、実機未確認 |
| `docs/architecture/b5/*` / `b7/*` / 8 schema | reviewed-no-change | DTO・wire・schemaは不変（`--check`）。B5 Repositoryの初回open待機は内部実装でAPI応答の形式を変えない。B7の同key replayは初回status（202）を再現する既存仕様のまま |
| `docs/architecture/ui3/s3a-legacy-transcription.md` / `s3b1-multi-input-and-admission.md` | reviewed-no-change | `--next-ui`の旧API投入は不変 |
| `docs/リリース手順.md` | reviewed-no-change | 規則どおりpatchを0.1.51へ（`pyproject.toml`・`uv.lock`）。手順変更なし |

## リンク検証

`tests/test_docs_links.py`が`AGENTS.md`、`README.md`、`README.en.md`、`docs/architecture/README.md`、
`docs/architecture/backend/*.md`の相対リンク（ファイル存在と見出しanchor）をCIで検査する。
