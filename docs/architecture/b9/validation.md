# B9 検証記録（2026-11-02、ローカルLinux container）

| 項目 | 修正前main | B8（0.1.37） | B9（0.1.38） |
|---|---|---|---|
| `pytest -m "not requires_model"`（3.11.18） | 1122 passed / 2 failed / 9 skipped | 1163 / 2 / 9 | **1184 passed / 2 failed（同一node） / 9 skipped** |
| 契約matrix（14ファイル） 3.11.18 / 3.12.3 | — | 569 / 569 | 590 passed・1 skipped / 590 passed・1 skipped |
| Ruff / format | — | 成功 | 成功（198 files） |
| mypy strict | 既知9件 | 既知9件 | 既知9件のみ（#27） |
| schema `--check` b1〜b9（8種） | — | 7種成功 | 8種成功 |
| `uv lock --check` | — | 成功 | 成功（root version行のみ） |
| wheel→isolated install→import・v5 store・sync（3.11/3.12） | — | 成功 | 成功（`0.1.38`、`TARGET_VERSION=5`） |

2 failedは`test_gui_processes.py::test_profile_manifest_detects_*`（mainでも同一。containerにWindows PowerShellがない）。

## 指示書§4 B9との対応（`tests/test_b9_events.py`、21件）

| 区分 | テスト |
|---|---|
| 1 epoch:seq・commit済みのみ | 単調・重複なし、commit前故障のopはeventなし、revision 1:1 |
| 2 cursor・SSE | framing（id/event/1行data）、header、keepalive、resource filter（gap可）、after/Last-Event-ID同値、不一致/不正/別epoch/未来/不正resource、cookie認証・Host拒否、購読者上限503、接続中epoch rotationで`resync_required`（idなし） |
| 3 sync | 1 read transaction（呼出し計測）、ページ間に更新・新規を挟んでも同一revision・欠落/重複なし、cursorのsession/token束縛・再起動409・`j2`転用拒否、HTTP（inventory_revision）、v3でnull |
| 4 順序逆転・再接続 | reducer（sync→event、重複・古いGET・gap・別epoch）、SSE再接続3回でsetupが起動しない |
| 5 compaction | 7日未満拒否・未満は削除なし、floor・410、保護resourceの行は保持、idempotent、trigger外DELETE不可、keep_latest、commit前crashで不変、v4→v5・旧クラス拒否 |
| 6 privacy | private payload sentinel（Prompt・path風）がSSE/sync/diagnosticsに出ない、setup diagnosticsにtoken・tmp path・argvなし |
| 7 既定app | route一覧golden（main時点）、旧wizard SSE維持、`/api/v1`なし、B9 module import なし |
| backpressure | 3 stream接続中に20件の書込みが5秒以内に完了 |

## 未検証

- 実ブラウザEventSource・proxy越しのbuffering、長時間（1時間）stream、10万件超の実データでのcompaction時間。
- Windows Actionsの結果はpush後に記録。

## PR #33レビュー修正の検証（2026-11-02追補、基準HEAD `8264ab1`）

提出時の上記記録は当時の事実として保持する（diagnosticsのcompaction後変化・offset pagingは検出していなかった）。

### 修正前の再現（`8264ab1`のコード、スクリプト実行）

- B9-P2-2: refresh operationの診断`attempt_id`が`[null, att_…, att_…]` → 8日後に`compact_events(keep_latest=0)`（3件削除）
  → `[null, null, null]`（CHANGED）。
- B9-P2-3: 時計固定で1ページ目（limit 2）取得後にhold/releaseを1回追加 → 1ページ目revision 4・2ページ目revision 5（混在）。

### 追加テスト（`tests/test_b9_review_fixes.py`、11件、修正後すべて成功）

| 区分 | 内容 |
|---|---|
| P2-2 | B7 Job（実dispatch、attempt有）・B8 Setup（attempt＋step journal）・attemptなしJobの診断が圧縮前後で全field一致（outbox行数の減少を確認）、同一setupの2 attempt・他resourceと混同しない（sidecar 1:1）、v5→v6 backfill（現存はbackfilled、圧縮済みはlost/NULL、operation_history不変）、migration故障でv5維持・4並列open・旧4クラスの拒否、v1→v6・sidecar/historyのimmutable、retention/floor/epoch不変 |
| P2-3 | 固定時計＋時計巻戻り＋ページ間追加でも重複/欠落なし・revision固定、compaction・setup journalを跨いでも固定、別resource/kind/session・署名/offset/limit改ざん・`d1`/`j2`転用は400、再起動/TTL/evictは409、上限超過503、HTTP（200件超の2ページ・revision固定・401/403/400） |

### 実測（Linux container、修正後HEAD）

| 項目 | 修正前HEAD `8264ab1` | 修正後 |
|---|---|---|
| pytest `-m "not requires_model"`（3.11.18） | 1184 passed / 2 failed / 9 skipped | **1212 passed / 2 failed（同一node） / 9 skipped** |
| 契約matrix（16ファイル）3.11.18 / 3.12.3 | 590 / 590 | **618 passed・1 skipped / 618 passed・1 skipped** |
| Ruff / format / mypy | 成功 / 成功 / 既知9 | 成功 / 成功（200 files） / 既知9のみ |
| schema `--check` 8種、`uv lock --check` | 成功 | 成功（events.schema.jsonは`next_cursor`最大長のみ再生成） |

wheel・public scan・GitHub Actionsの結果はPR本文「Review fixes」に記録する。
