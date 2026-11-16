# B7 — 履歴の非表示（layout v7）と成果物の読み取りビュー

更新: 2026-11-16 JST（開発版`0.1.55`・未リリース、束C2a）。全体設計は[../program/c2-history-and-artifacts-design.md](../program/c2-history-and-artifacts-design.md)。

> この束は**読み取りと非表示だけ**。何も削除しない。履歴レコードの削除（墓標化）と成果物削除は別PR（束C2c）で、未実装。
> Windows実機は未確認。

## 1. 何をするか

| 追加 | 内容 |
|---|---|
| `GET /api/v1/jobs?visibility=visible\|hidden\|all` | 履歴一覧のfilter。layout v7の既定は`visible` |
| `GET /api/v1/jobs/{id}/visibility` | `{job_id, hidden, revision, updated_at}`。ETag＝visibility revision |
| `PUT /api/v1/jobs/{id}/visibility` | `{"schema_version":1,"hidden":true\|false}`、`If-Match`必須 |
| `GET /api/v1/jobs/{id}/artifacts` | 成果物の形式・サイズ・照合結果（読み取り専用、名前とpathは返さない） |

## 2. 非表示は「表示の属性」であり、Jobの状態ではない

非表示にしても変わらないもの（試験`test_hiding_changes_only_the_visibility_row`が、既存16表の全行が非表示の前後で同一であることを確認）:

- Jobの`status`・`revision`・`operation_history`、snapshot、attempts、`job_results`、キュー、counters。
- 冪等replay（`replays`/`queue_replays`/`batch_replays`/`job_replays`）。
- **Outbox**（イベントを書かない）。B9 `/events`・`/sync`・diagnosticsの出力。
- `GET /api/v1/jobs/{id}`（非表示のJobもIDで読める）、retry・rerun（`retry_of`/`rerun_of`の連鎖は非表示のJobを指したまま有効）。
- ユーザーの出力先のファイル。

### 2.1 更新方式（追加した表）

```sql
CREATE TABLE job_visibility (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id),
  hidden INTEGER NOT NULL CHECK (hidden IN (0,1)),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  updated_at TEXT NOT NULL);
-- DELETEはトリガーで禁止（行を消さないので、revisionが再利用されない）
```

行が無い＝一度も設定していない（`hidden=false`、`revision=0`）。変更のたびに同じ行を上書きして`revision`を1増やす（`job_state`と同じ「可変の付帯表」）。
履歴の追記表にはしていない（誰がいつ隠したかの監査は要件に無く、行が増え続ける表を増やさないため）。

### 2.2 CAS

- `If-Match`は**visibilityのrevision**（Jobのrevisionではない）。Jobの状態が進んでも非表示操作は競合しない。逆に非表示操作でJobのETagは変わらないので、
  進行中の取消・再試行のCASを壊さない。
- 状態を**実際に変える**要求は、現在のrevisionと一致しなければ412 `revision_conflict`。
- **既に同じ状態**への要求は、`If-Match`が古くても200で現在の表現を返す（何も書かない）。応答を受け取れなかったときに同じ要求を
  そのまま再送してよい（冪等なPUT）。そのためIdempotency-Keyとreplay表は使わない（replay表に行を増やさない）。
- 非表示にできるのは終了したJob（`succeeded`/`failed`/`cancelled`/`interrupted`）だけ。待機中・実行中は409 `invalid_transition`。再表示は常に可能。

### 2.3 Outboxとの整合

- 非表示は**イベントを出さない**。B9のイベントは「操作の状態」と「キュー」の変化だけを運ぶという既存の定義を変えない
  （`state_changed`/`queue_changed`以外の型を足すと、Python/TSの`EventReducer`・`/sync`・共通fixtureの改訂が必要になる）。
- したがって履歴一覧の正本は`GET /api/v1/jobs`。画面は非表示操作の応答で自分の表示を更新し、他の表示は次のGETで追従する。
  同じdata rootのGUIはOSロックで1つなので、別プロセスの同時表示は想定しない。
- 一覧のcursorは発行時点のスナップショット（既存の性質）。途中で非表示にしても発行済みcursorの中身は変わらない。cursorのMACにfilterを含めたので、
  別のfilterでは`cursor_invalid`。

## 3. layout v7への移行・backup・downgrade

- v7 = v6 ＋ `job_visibility`（表とトリガーの**追加だけ**。既存の表・行・トリガーは無変更。試験で移行前後の全行が同一であることを確認）。
- 移行は既存の仕組みどおり1つの`BEGIN IMMEDIATE`内で行い、失敗すればv6のまま残る（試験あり）。
- **一方向**。v7のファイルは0.1.54以前のbuild（v6までのclass）では開けない（`store_schema_unsupported`で拒否し、ファイルは変更しない）。
- **backup**: v1 runtimeの起動（`JobRuntime.activate()`）は、storeを開く**前**に毎回`operations-pre-v1-<時刻>.sqlite3`を作る（既存の挙動）。
  0.1.55で初めて起動したときのbackupはv6のままで、0.1.54のbuildで開ける（試験で復元ドリルを実施）。
- **旧buildへ戻す手順**: アプリを終了 → backupを`operations.sqlite3`へコピー → 旧buildで起動。backup取得後に作ったJob・非表示の設定は失われる。
  backupにはPrompt・入力path等が平文で含まれるので共有しない（既存方針）。
- v1は開発専用（`--next-ui-v1`、専用markerのdata root）で未リリース。既定起動（旧GUI/CLI）はcontrol DBを作らない・開かない、のまま。
- B8を構成しないruntime（layout v3、試験と一部の開発構成）には非表示が無い: `visibility`系のrouteは409 `history_visibility_unavailable`、
  一覧は従来どおり全件（`visibility=all`以外の指定は422）。黙って無視しない。

## 4. 成果物の読み取りビュー

`GET /api/v1/jobs/{id}/artifacts`は、束C1の専用ディレクトリのうち**所有markerがそのJob IDと一致するもの**だけを見る（どのID長でも）。書き込み・削除はしない。

| `state` | 意味 |
|---|---|
| `available` | 専用ディレクトリが見つかった。`items`に形式・サイズ・`integrity` |
| `missing` | 試行済みなのに見つからない（移動・削除された、markerが別Jobのもの等） |
| `legacy` | 0.1.54より前に受理されたJob（出力先へ直接書いたため、どれが成果物か証明できない） |
| `none` | まだ試行していない |

- `integrity`: `verified`＝完了時に記録したsha256と一致、`changed`＝一致しない（編集・差替え）、`unverified`＝照合する記録が無い（成功していないJob等）。
- `unexpected_count`: ディレクトリ内の、Utteranが書いていないファイル・サブディレクトリの数。束C2cの成果物削除で「既定は拒否」とする判定の元になる。
- **ファイル名・ディレクトリ名・pathは返さない**（名前には入力ファイル名が含まれるため。公開DTOは値を持たない方針のまま）。
  「フォルダを開く」と本文の表示（Viewer）は束C2bで、pathをserver内で解決する形にする。

## 5. 試験

`tests/test_history_visibility.py`（14件）: v6→v7の追加のみ移行・旧classの拒否・失敗時rollback・起動時backupと復元ドリル、非表示で既存表が不変、
CASと冪等な再送、終了したJobだけ、filterとcursorの束縛、v7より前のlayoutでの明示的な拒否、非表示のJobからのretryとB9 snapshot、
HTTPの往復・ガード・余分なfieldの拒否、成果物ビュー（形式/サイズ/照合、編集・想定外ファイルの検出、名前とpathを返さない、読み取りでファイルが変わらない、各state）。
