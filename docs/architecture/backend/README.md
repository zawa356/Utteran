# Backend — current state (正本)

更新: 2026-11-03 JST。開発版 **0.1.39（未リリース）**。基点 `main` = `e803ecc3`（PR #33 B8+B9 merge）。

この文書群は**コードから逆引きした現在の実装**を記述する。Phaseごとの詳細仕様は各`bN/`へリンクする。
読む順番は[../README.md](../README.md)。

## 統合状態

| Phase | 内容 | main統合 | リリース |
|---|---|---|---|
| A | 契約・lifecycle・OpenAPIの**提案** | PR #30 `7318a2f` | — |
| B1/B2 | 共有DTO（`utteran_contracts`）／presentation非依存application | PR #30 | 未 |
| B3 | 1 request profile worker（`python -m utteran.worker --protocol 1`） | PR #24 `cf84f33` | 未 |
| B4 | read-only capability inventory・pure resolver | PR #25 `417e3be` | 未 |
| B5 | Preset/Policy履歴store・preview（`/api/v1` opt-in） | PR #26 `8a3f175` | 未 |
| B6 | 永続control store・transactional Outbox | PR #28 `c636555` | 未 |
| B7 | 不変Job投入・永続Queue・単一slot dispatch | PR #32 `4fbb4d1` | 未 |
| B8 | Setup Plan・承認・永続Setup実行 | PR #33 `e803ecc` | 未 |
| B9 | SSE `/events`・`/sync`・diagnostics・Outbox compaction | PR #33 `e803ecc` | 未 |
| U0〜U3 | 新UI | **未着手** | — |

**B7〜B9はmain統合済み・未リリース・opt-in**。通常のGUI起動（`utteran_gui/app.py` → `create_app(session_key,
repo_root=...)`）は**どれも有効化しない**。v1経路は`create_app(..., enable_v1_*=True)`を明示的に組み立てた
場合（現状はテストと開発者の明示構成のみ）に限り存在する。

## 機能ごとの提供状況

凡例: ✅ 実装済み（モデル不要テストで検証）／◐ 限定実装／— 未実装／⚠ 実機未検証

| 機能 | 状態 | 注意 |
|---|---|---|
| 旧GUI/CLI（`/api/*`、in-memory `OperationQueue`、旧wizard） | ✅ | 既定経路。v1 opt-in中は旧queueが閉じ、旧wizard/jobs投入は409 |
| B5 Preset/Policy CRUD・preview（`/api/v1/presets` 等） | ✅ | 保存成功は実行readyを意味しない |
| B7 execution resolution / Job batch投入 / Queue CAS制御 | ✅ | — |
| B7 Job実行（B3 worker dispatch） | ◐ ⚠ | B4のready証拠が必要。**標準collectorにsmoke providerが無いため、実機では通常 409 `execution_unavailable`（fail closed）**。旧CLIへのsilent fallbackはしない |
| B7 force cancel / 汎用retry以外の高度な操作 | — | `cancel_mode_unsupported` |
| B8 Setup plan（read-only）/ approval / operation | ✅ | plan作成に副作用なし |
| B8 Setup実行（モデルverify/download、Windows新規profile作成、credential preflight、smoke） | ◐ ⚠ | fake child runnerでのみ検証。**実setup.ps1・GPU・実モデルDLは未受入**。whisper.cpp(native build)・OpenVINO・既存venv rebuild・B4登録は拒否。setup成功はB4 readyに昇格しない |
| B9 SSE `/events` | ✅ ⚠ | TestClientでframing・cursor・再接続を検証。**実ブラウザ`EventSource`/WebView2は未受入** |
| B9 `/sync`（1 read transaction・snapshot paging） | ✅ | — |
| B9 diagnostics（固定code・時刻・stage・attemptのみ） | ✅ | — |
| Outbox compaction（7日超・最新100,000件より古い部分） | ✅ | 明示activate時のみ。background loopなし |
| progress / inventory_changed / fallback イベント | — | 永続証拠が無いため配信しない |
| 起動時recovery（paused・held・`recovery_required`） | ✅ ⚠ | 実process kill・Windows ACL・reparse判定は実機未受入 |
| private DBの暗号化・ACL方針・retention・完全削除 | — | **owner判断待ち**（[persistence-recovery.md](persistence-recovery.md#privacy)） |

## 既知の制約（要約）

- **fail closed**: 未検証の構成・未知のschema・確認できないownerは実行せず、409/`recovery_required`で止める。
  未対応backendへのsilent fallbackは禁止。
- **migrationは一方向**（v1→…→v6）。旧classはv6を開けない。旧版へ戻すにはactivate時の
  `operations-pre-v1-*.sqlite3`（0.1.39から**migration前のlayoutのまま**保存）を復元する。復元はtmp DBで
  テスト済みだが、実機での復元手順は未受入（[persistence-recovery.md](persistence-recovery.md#backup-と-restore)）。
- **privacy**: private DBにはPrompt・入力path等が**平文・無期限**で残り得る。公開DTOはallowlist、ログは抑制、
  `/api/v1`はsession＋Host＋Origin検証。DBのbackupも秘密を含むので公開・共有しない。
- **実機ゲート**（Windows実機、GPU、native build、実モデルDL、長時間音声、installer/portable、WebView2）は
  本ブランチの完了条件に含まれず、**確認済みではない**（[testing-and-operations.md](testing-and-operations.md#未受入ゲート)）。

## 次に読む

- 構成と依存: [components.md](components.md)
- API・状態遷移: [interfaces.md](interfaces.md)
- DB・復旧・privacy: [persistence-recovery.md](persistence-recovery.md)
- CI・E2E・運用: [testing-and-operations.md](testing-and-operations.md)
