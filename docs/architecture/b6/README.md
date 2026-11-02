# Phase B6 — Durable Operation Store / Transactional Outbox

2026-11-02 JST、開発版0.1.35、**Draft・未マージ・未リリース**。
B5 [Draft PR #26](https://github.com/zawa356/Utteran/pull/26)（OPEN/Draft、base B4）のHEAD
`5abb23d62f5fd061c8f5d88ea25d0c761fb133d2`を再確認し、そこから分岐した。PR baseは
`feat/b5-preset-policy-editing-api`（mainではない）。B5ブランチへは直接pushしない。

作業ブランチ名は指示書の推奨`feat/b6-durable-operation-store-outbox`ではなく、本セッションに
割り当てられた`claude/loving-noether-jroqrc`を用いる（push先がセッション設定で固定されているため）。
基点・base・差分の意味は同一で、詳細は[compatibility](compatibility.md)に記録する。

## 範囲

B6は**安全に状態を残す基盤**であり、ジョブ制御（B7）、セットアップ（B8）、公開配信（B9）ではない。

| 作るもの | 作らないもの（後続） |
|---|---|
| 独立`utteran_control` package、`DataPaths.control_store` | 旧`OperationQueue`置換、旧queueからの移行（B7） |
| operation受理、kind別遷移表、CAS revision | 実Job投入、batch submission、reorder API、dispatch（B7） |
| queue metadata（順序、held/blocked、単一active slotのDB制約） | scheduler、worker割当、retry/rerun/fallback（B7） |
| HMAC冪等性replay（Operation・Queue dispatch、再起動後も同一結果） | Setup plan/実行、venv/model/native（B8） |
| 公開Outbox、`<epoch>:<seq>` cursor、snapshot+watermark | `/api/v1/events` SSE、`/api/v1/sync`、互換API（B9） |
| owner lease、fencing token、attempt journal、launch evidence | 子process起動・kill・OS生存確認の実装（B7） |
| 注入Verifierによる保守的recovery（明示呼出し） | 自動recovery、lost leaseからの再dispatch |
| online backup、epoch更新、B6 offline schema | GC/compaction、保持期限ポリシー決定 |

既定GUI/CLI（`create_app()`含む）はcontrol DBを作成・importしない。HTTP routeは追加していない。

## モジュール

| ファイル | 責務 |
|---|---|
| `src/utteran_contracts/control.py` | B6 DTO（strict、unknown拒否、`schema_version=1`、UTC aware datetime）、状態/理由enum |
| `src/utteran_contracts/control_schema.py` | 決定的offline schema exporter（`--check`対応） |
| `src/utteran_control/store.py` | SQLite store、transaction、CAS、replay、Outbox、snapshot、recovery適用、backup |
| `src/utteran_control/transitions.py` | Phase A lifecyclesに基づくkind別遷移表 |
| `src/utteran_control/recovery.py` | `ProcessVerifier` protocol、`Liveness`、純粋な判定`decide` |
| `src/utteran_control/errors.py` | 固定error code（想定HTTP statusつき、値・path・SQLを含まない） |

`utteran_control`は標準library、Pydantic、`utteran_contracts`のみに依存する。
`utteran`/`utteran_gui`/`utteran_settings`/torch等はimportしない（テストで検証）。

## 資料

- [保存・schema・migration・backup](storage.md)
- [transaction・CAS・冪等性](transactions.md)
- [Outbox・cursor・snapshot](outbox.md)
- [lease・fencing・recovery](recovery.md)
- [security/privacy](security.md)
- [互換性・意図的差異・後続への拡張点](compatibility.md)
- [検証結果と未検証事項](validation.md)
- [文書監査](docs-audit.md)
- [PR #28レビュー修正の検証](pr28-review-fixes-validation.md)
- offline schema: [control.schema.json](control.schema.json)、合成例: [examples.json](examples.json)

## DoD / 文書ゲート（後続Phaseへ継承）

- テスト成功だけでPhase完了としない。README日英・変更履歴・AISTATE・Phase資料・Issueを再監査し、
  [docs-audit](docs-audit.md)を`updated`/`reviewed-no-change`/`blocked`で更新する。
- 「永続化したから復旧できる」「cursorがあるからSSE」「queue表があるからdispatch」という飛躍をしない。
  unknown/paused/recovery_requiredを、誤った成功や重複起動より優先する。
- Draft維持。Ready化・merge・rebase・tag・release・B7着手はしない。
