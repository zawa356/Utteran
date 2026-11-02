# レビュー判断・リスク・検証記録

状態: **設計案 / レビュー待ち**。2026-10-30、日本時間。コードの実装完了・実機合格・リリースを意味しない。

## 1. 実装前の判断事項

| ID | 判断・リスク | 推奨案と理由 | 採択・実証の担当/時点 |
|---|---|---|---|
| D1 | 軽量共通DTOのpackage境界 | `utteran_contracts`をGUI/core両者の下へ置く。core importを避けながら形を共有 | ユーザー設計承認、B1 import graph/package test |
| D2 | 永続control store | SQLite + transaction/outbox、単一owner/slot。既存core manifestは維持 | ユーザー承認、B6 Windows crash/lock/portable受入 |
| D3 | setup承認の粒度 | hash付きplan全体を承認、変更は再承認。未承認大容量downloadを防止 | ユーザー承認、B8 UX/API test |
| D4 | 手動固定とfallback | fixedはfallback none。autoもsnapshot内の事前承認済み構成のみ | ユーザー承認、B4/B7 |
| D5 | private Prompt/用語集の履歴保存 | 再現性のためlocal ACL付きsnapshotに保存、token除外、通常exportはredacted | ユーザーの保持方針判断、B5/B7。portable可搬媒体での暗号化は別設計 |
| D6 | 実験的unknown引数 | 安全な文法/非reservedの範囲でExpert明示許可。初期実装で全unknown拒否する縮小案も妥当 | ユーザー判断、B2/B3 parser脅威試験。registry未整備で全passは禁止 |
| D7 | frontend framework/UI kit | Vue/PrimeVueを含む候補比較を維持。今回選定しない | 独立U0 ADR、Windows VM spike後にユーザー採択 |
| D8 | retention/規模 | event 7日/100,000件、idempotency最低7日・activeは保持、batch最大1000案 | ディスク量/履歴privacy/大規模利用をレビュー、B6負荷試験 |

D1〜D8は本PRをmergeするだけで自動採択済みにしない。必要な方針合意をPR reviewに残してから後続実装へ進む。

## 2. 技術検証が必要な事項

| リスク | 現時点の確実なこと / 不明なこと | 後続の検証・禁止事項 |
|---|---|---|
| Prompt品質 | 内部配線あり。no_context+promptの有効性は未実測 | #1 synthetic A/B。反復抑制を安易に無効化しない |
| native argv privacy | worker stdinでGUI→coreのargv露出は減らせる。whisper.cppは現行--promptで渡す | nativeの保護input経路spike。全経路非露出を未検証で保証しない |
| 実route観測 | requested variantとencoder/decoderの実初期化は異なる | native/runtime logの許可field解析、unknownを保持 |
| smoke/性能 | smokeは1条件の実行証拠で品質/速度順位ではない | fixture/model/precision等の同条件benchmark群のみ比較 |
| bootstrap/core非依存 | 初回はprofile coreがない。shared DTOだけではruntime判定できない | 軽量OS probe +同梱recipeでplan、構築後core確認。heavy importによる解決禁止 |
| Windows venv generation | venvは移動で壊れ得る、旧manifestもpathを検査 | 最終generation pathに構築しlogical pointer更新。既存layoutの移行は明示承認、旧環境保持 |
| shared native資源 | buildはprofile横断、runtime DLLはactive venv依存 | resource fingerprint/lease、破壊的再buildは他profileへ影響を評価 |
| cancel/hard kill | GenAI途中native callは即停止できない場合がある | #12実機latency、cache/job破損・resume検証までforce capabilityをfalse扱い |
| DBとcore fileの境界 | 単一DB transactionでもchild開始/外部file書込みはatomicにならない | attempt journal/nonce/artifact reconciliationで復旧、exactly-once推論を主張しない |
| process cleanup | containerではkill済みchild zombieがPID検査に残り得る | Linux runtime PID 1/reapを別診断、Windows tree/Job Object実機受入 |
| GUI操作の症状 | PR #23はisolated JS stale応答再現を報告、今回再実行していない | VM browser/配布版の再現、ユーザー症状の原因と即断しない |
| distribution/security | contractsやTS生成の導入がGUI配布を変える可能性 | local assets/CSP、WebView2、keyring/session、禁止import/同梱license、#7/#15/#22 |

## 3. Phase Aで行った調査・検証

基点main `1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`。Git fetchで確認し`docs/phase-a-architecture`を作成。PR #23はGitのpull refを読み、head `810c5c9fda118f34fb0a011c7be221b7f8bfe15b` のADRだけを参照。指定9 IssueのbodyをWebから取得（#19のコメント本文・PRレビュー議論/CIは未取得）。GUI/backend/core、README・要件定義・AISTATE・刷新計画・release方針の関連箇所を照合。

- `git diff --cached --check`: 成功、差分はdocs配下10ファイルのみ。
- 公開worktree scan: 249 tracked files、72 findings、blocking 2。blockingは既存の合成WAV fixture 2件（今回追加文書にはblockingなし）。PowerShell BOM checkは既存`verify_uninstaller.ps1`で失敗。どちらも本Phaseで修正していない。
- 本番Python/JS/HTML/CSS、テスト、依存宣言/lock、データ配置の変更なし。新しい機能テストやmigrationも実装していない。
- ASTによる主要GUI4moduleのcore import確認: `api.py` / `cli.py` / `jobs.py` / `setup_wizard.py`に`utteran` importなし。
- OpenAPI 3.1 validator、JSON Schemaで合成例/不正例、内部$ref、operationId一意、文書リンクを検証。検証用依存はcheckout外venvに導入しproject lockは変更しない。
- `uv lock --check --offline`: 成功。
- `ruff check src tests tools`: 成功。
- `ruff format --check src tests tools`: 119 files、成功。
- `mypy`: **失敗**、`src/utteran_gui/processes.py`のWindows専用ctypes API属性9件。今回未変更、onboarding時の同mainでも同じ失敗。
- モデル不要pytest: **473成功 / 3失敗 / 8 skip / warning 1（15.64秒）**。失敗は`test_timeout_kills_probe_process_tree`とprofile manifest test 2件。後者はWindows専用fixtureをLinuxで参照、前者はkill後childがPID 1配下のzombieとして残りPID存在判定がtrueになる環境条件。`/tmp/.git`による出力保護への干渉を避け`/var/tmp/utteran-phase-a-tests`を使用。モデルをダウンロードしていない。

Phase Aの新APIは未実装のため、schema検証を実API/IPCの動作保証としない。Windows VM/browser E2E、GPU/native/Prompt A/B、installer/portable、hard cancelは今回未実施。以前のonboardingでのCLI/GUI HTTP smokeは別作業の証跡であり、今回新機能のテストではない。

## 4. 完了判定の範囲

監査、依存方向、継承、capability/selection/fallback、共通setup、job/queue/state、API/SSE/IPC、Prompt/args、互換移行、小PR/受入/未検証の設計成果を提出する。Draft PR作成結果は最終報告に記載する。APIネットワーク等で作成不可の場合は未完了として明記し、URLを捏造しない。設計承認、main merge、Ready化、実装開始はユーザー判断に残す。

### 機械可読契約の検証結果

`openapi-spec-validator 0.9.0` / `jsonschema 4.26.0`でOpenAPI 3.1構文・参照を検証。32 paths、41 operations、74 schemas、608 internal refsを確認。12個の合成正例（preset/policy/editing/resolution/submission/error/event/job/setup request/setup plan/approval request/queue）を各schemaへ適用。不正fixed policy 2例、未知schema version、queue ID重複、eventへのraw log、beam_size null、非対応backend追加argsの7不正例が拒否されることを確認。

これはschemaで表せる制約の検証。leaf overrideの意味、revision transaction、実行可能性、setup DAG、native安全性は将来のservice testで検証する。JSON例はprivate値を含まない合成値で、例中model/candidateは実在・導入済みを意味しない。

検証の再実行例（validatorは開発用環境へ導入し、project依存を更新しない）:

```console
python -m openapi_spec_validator docs/architecture/phase-a/openapi.yaml
```

examples.jsonのキーとschemaの対応は上記の通り。JSON Schema Draft 2020-12でOpenAPI全体を基底resourceとして登録し、`#/components/schemas/<name>`へ各例を検証する。文書リンクとoperationId一意性も検査済み。

### 提出状態・外部ブロッカー

`docs/phase-a-architecture`をoriginへpush済み。`gh pr create --draft`は`https://api.github.com/graphql`への通信がForbiddenとなり失敗し、**Draft PR未作成**。GitHub HTTPS Git read/pushとWeb Issue readは成功しており、GitHub tokenが欠けているとは断定しない。API destinationへのCONNECTも403を確認した。環境設定draftへ`api.github.com`を追加保存したが、実行環境のallowlistへの反映は未確認。環境設定で追加を保存・反映後、API到達性を確認してPR作成を再実行する必要がある。

PR本文は現在の作業環境の`/workspace/.utteran-cloud/phase-a-pr.md`にも保存済み（リポジトリ管理外）。復旧後の操作:

```console
gh pr create --repo zawa356/Utteran --base main --head docs/phase-a-architecture --draft --title 'docs: Phase A アーキテクチャ再設計・API契約案' --body-file /workspace/.utteran-cloud/phase-a-pr.md
```

先に同headのPR有無を確認し、重複作成を避ける。通信制限の解消までは指示書DoDのDraft PR項目を未完了とする。ブランチ比較ページはPRそのものではない。mainへのpush、既存Issue/PRのclose、PR #23の変更、Ready化・mergeは行っていない。
