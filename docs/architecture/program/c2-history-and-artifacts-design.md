# 束C2 設計案 — 履歴の非表示・履歴レコードの削除・生成成果物の削除

更新: 2026-11-16 JST。状態: **owner確定（§0）**。束C2a（読み取り・非表示）は実装済み（`0.1.55`、[b7/history-visibility](../b7/history-visibility.md)）。
履歴レコードの削除（墓標化）は束C2c `0.1.57`で実装（Draft PR、Windows未受入）。**成果物削除は2026-11-18のオーナーレビューにより全環境で無効**（安全性を証明できないため）。
**現行の設計・実測は[c2c-deletion-design.md](c2c-deletion-design.md)を正本とする**（§3以降の旧案と異なる点: 一時表ではなく永続ゲート表〔一時表はSQLiteで不成立を実測〕、
削除済みJobは410、APIは`deletion-plans`/`deletions`の2本、成果物削除は全環境で拒否）。以下は確定時の設計記録として残す。

## 0. owner確定（2026-11-16）と必須条件

- ②非表示の先行実装とv7 migrationを承認。③は**③-a 墓標化**（行全体の物理削除は必須としない）。
  ④は未知のファイル・編集済み成果物があれば**既定で拒否**。「完全消去」とは表示せず、backup・WAL等への残存可能性を明記する。
- 分割: **C2a** 読み取り＋非表示（backend）→ **C2b** 複数ジョブUI（論理ジョブ、キュー全件、履歴、非表示）→ **C2c** 破壊的削除（別PR、十分な試験）。
- 合成データでのみ実装・試験する。既存の実ユーザーデータは削除・改変しない。

| # | 必須条件 | 対応 |
|---|---|---|
| 1 | 非表示テーブルの更新方式とCAS・Outboxの整合性を明確にする | **C2aで実装・文書化**: [b7/history-visibility §2](../b7/history-visibility.md)（付帯表の上書き＋独自revision、冪等PUT、イベントなし） |
| 2 | 墓標化が既存snapshot schema・retry/rerun・replay・B9同期・履歴参照を壊さない | C2cの設計条件（§3 ③-a）。C2aの試験`dump`比較（既存16表の全行）をC2cでも基準に使い、墓標化後に各読み取り経路が通ることを試験する |
| 3 | 削除後にprivate DB・WAL・replay等へ残る情報を確認する | C2cで、sentinelを入れたJobを墓標化し、DB本体・WAL・各replay表・backupを走査する試験を必須にする。残るものは文書と画面文言に列挙する |
| 4 | 成果物削除は所有権と内容を**実行直前にも**検証し、想定外はfail closed | C2cの設計条件（§3 ④）。preflight時と実行時の2回、同じ検証関数を通す。C2aの読み取りビュー（`integrity`・`unexpected_count`）が検証の部品。**照合は形式別の記録だけ**（PR #52 P2-2。Job全体のdigest集合との照合は使わない。記録なし＝`unverified`＝拒否）。Windowsのsymlink/junction/reparse pointは削除前に別途厳格に検証する |
| 5 | 一方向migrationに備え、backupと旧buildへのdowngrade不可を文書化 | **C2aで文書化・試験**: [b7/history-visibility §3](../b7/history-visibility.md)、[backend/persistence-recovery](../backend/persistence-recovery.md) |
前提: 束C1（[b7/output-directory](../b7/output-directory.md)）、retryの整理（[retry-semantics](retry-semantics.md)）。

## 1. 区別する4つの操作

指示書 §6とDesign Freezeは、次の操作を**別物**として扱うことを求めている。本設計は混同できない形（別のAPI・別の確認）にする。

| # | 操作 | 消えるもの | 残るもの | 元に戻せるか |
|---|---|---|---|---|
| ① | **キューから片付ける** | キュー表示の行 | Job・snapshot・履歴・成果物 | — （既存の挙動。終了したJobは`queue_items`から外れ、`GET /jobs`には残る） |
| ② | **履歴から非表示にする** | 履歴一覧での表示 | Job・snapshot・成果物・control DBの全行 | **戻せる**（再表示） |
| ③ | **履歴レコードを削除する** | そのJobのprivateな内容（snapshot本文＝入力path・Prompt・用語集、試行の記録） | 成果物（ユーザーの出力先のファイル） | 戻せない |
| ④ | **履歴＋生成成果物を削除する** | ③に加え、そのJobの出力ディレクトリ | 元の音声/動画、作業セット、他Jobの成果物、ユーザーが置いたファイル | 戻せない |

**どの操作でも対象外（絶対に触らない）**: 元の音声・動画、作業セット、Preset/Policy、他Jobのディレクトリ、束C1より前の
「出力先へ直接書かれた」成果物（所有を証明できないため。§5）。B9 Outboxの圧縮は別概念で、履歴保持と連動させない。

## 2. 現在のcontrol DBの制約（実コード）

- `job_snapshots`・`job_results`・`operation_history`・`outbox`・`replays`・`batch_replays`・`job_replays`は、
  `BEFORE UPDATE/DELETE`トリガーで**不変**（`RAISE(ABORT,'immutable')`）。Jobを消す操作は存在しない。
- `job_snapshots.retry_of`/`rerun_of`、`job_results.attempt_id`、`attempts`、`queue_items`は`operations(id)`を参照している。
- layoutはv1→v6の**一方向migration**。変更は新しい版（v7）の追加になり、旧buildはv7を開けない。
- private DBの保持・削除・ACL方針は以前から**owner判断待ち**（[backend/persistence-recovery](../backend/persistence-recovery.md#privacy)）。

## 3. 設計案

### ② 非表示（推奨: 最初に実装する。DBの既存表は無変更）

- 新しい**追記専用の表**`job_visibility(operation_id PK REFERENCES operations(id), hidden_at, revision)`を追加する（v7の追加migration。
  既存の表・行・トリガーは変更しない。行の追加と削除だけで、snapshot等の不変性に触れない）。
- API（案）: `PATCH /api/v1/jobs/{id}/visibility` `{"hidden": true|false}`、If-Match＋Idempotency-Key。一覧は既定で非表示を除き、
  `GET /api/v1/jobs?include_hidden=true`で含める。複数選択はclientが1件ずつ送る（1件ごとに結果が確定する。原子的な一括は不要）。
- 実行中・キュー中のJobも非表示にできるが、キュー表示には出続ける（キューと履歴は別）。
- **代替（migrationなし）**: 非表示の集合をcontrol DB外のprivate JSON（原子的置換）に持つ。DB layoutは変わらないが、
  CAS・冪等性・B9イベントとの整合を別途作る必要があり、推奨しない。

### ③ 履歴レコードの削除（owner承認が必要: 不変トリガーの方針変更）

「行を物理的に消す」か「中身だけ消して墓標を残す」かで設計が分かれる。

| 案 | 内容 | 利点 | 懸念 |
|---|---|---|---|
| **③-a 墓標化（推奨）** | `operations`の行と状態遷移の骨格（ID・状態・時刻・固定code）は残し、**privateな内容だけを消す**: `job_snapshots.body`を「削除済み」を表す固定の最小JSONへ置換、`operation_private`・`attempt_private`の該当行を削除、`job_replays`/`batch_replays`の応答bodyを削除。専用の`job_deletions(operation_id, deleted_at, mode)`に記録 | `retry_of`/`rerun_of`の参照、B9の`/sync`とイベントの整合、冪等replayの「同keyは同じ結果」が保てる。入力path・Prompt・用語集は残らない | 不変トリガーに「削除手続きだけ通す」例外が要る（§3.1）。「レコードが1行も残らない」ではない |
| ③-b 物理削除 | `operations`以下の全行をDELETE | 何も残らない | 外部キーで連鎖する表が多く、retry/rerunの連鎖・Outboxの過去イベント・replayと矛盾する。B9のclientは「存在したJobが消えた」イベントを新設しないと整合しない。SQLiteは`VACUUM`しない限りページに内容が残り得る |

#### 3.1 不変トリガーの扱い（③-aの場合）

- 不変の対象を「**通常の実行経路から変更できない**」と定義し直し、削除は専用の1トランザクション内でだけ行う。
  実装は「削除手続き用の一時表にIDがあるときだけ通すトリガー条件」にする（任意のUPDATEを通す汎用の抜け道は作らない）。
- 削除できる状態: `succeeded`/`failed`/`cancelled`/`interrupted`の**終了状態だけ**。`running`/`cancelling`/`recovery_required`/
  leaseを持つJobは409。「実行中は安全に停止・終了してから削除」は、**取消→終了の確認→削除**をUIが段階的に行い、APIは暗黙に止めない。
- `secure_delete`相当: 削除トランザクション後に`PRAGMA secure_delete=ON`でのページ上書きを使う。WAL・`operations-pre-v1-*`等の
  **backupには残り得る**ので、UIと文書で「完全消去」とは表現しない（指示書 §7）。

### ④ 生成成果物の削除（owner承認が必要: ユーザーの出力先のファイルを消す）

- 対象は**束C1の専用ディレクトリだけ**。次の全条件を満たすときに限る（1つでも外れたら何も消さず409 `artifact_scope_unverified`）。
  1. Jobのsnapshotに`output_stamp`がある（0.1.54以降に受理）。
  2. ディレクトリ名が`job_output_name`のいずれかのID長と一致し、親がsnapshotの`file.output_dir`と一致。
  3. ディレクトリ・markerがsymlink/reparse pointでなく、`.utteran-job`の内容が**そのJob ID**と一致。
  4. 中のファイルが「marker」と「`<ディレクトリ名>.<srt|vtt|json|txt|md>`」**だけ**。それ以外（ユーザーが後から置いたファイル、
     サブディレクトリ）が1つでもあれば、**既知のファイルだけ消してディレクトリは残す**か**全体を拒否する**かを選ぶ → **既定は拒否**を提案。
  5. `succeeded`のJobは、消す前に成果物のsha256を`job_results.evidence`と照合できる（一致しないファイル＝ユーザーが編集した可能性 →
     既定は拒否し、画面で「編集されています。それでも削除しますか」を別確認にする）。
- 消し方: ファイルを1つずつunlink → marker → 空になったら`rmdir`。`shutil.rmtree`は使わない（想定外の中身を消さないため）。
  ごみ箱（Windows）へ送る選択肢はホスト依存なので、Windows受入の所見を見てから決める。
- 順序: **成果物を先に消し、成功した分だけ履歴へ進む**。途中で失敗したら履歴は残す（「履歴は消えたのに成果物が残って、
  どのJobのものか分からない」状態を作らない）。結果は「消した数／残った数／理由code」で返す。
- 0.1.54より前のJob（出力先へ直接書いた成果物）は所有を証明できないため、**④の対象外**（③までは可能）。画面で理由を表示する。

### 複数選択

- client側で1件ずつ、同じ確認ダイアログの中で順に実行する。全件のpreflight（対象・件数・消えるファイル数の一覧）を先に表示し、
  **「履歴のみ削除」と「履歴＋成果物を削除」は別のボタン・別の確認文**にする。既定フォーカスは取消側。
- 途中で失敗したら停止し、済んだものと残ったものを表示する（S3-B1の順次投入と同じ考え方）。

## 4. 公開API（案、未実装）

| method | path | 内容 |
|---|---|---|
| GET | `/api/v1/jobs/{id}/artifacts` | 成果物の**名前・形式・サイズ・存在/照合結果**（allowlist。絶対pathは返さない）。結果Viewer・「フォルダを開く」の前提 |
| POST | `/api/v1/jobs/{id}/artifacts/open` | ホスト経由で専用ディレクトリを開く（pathはserver内で解決。既存`/api/open-folder`と同じ検査） |
| PATCH | `/api/v1/jobs/{id}/visibility` | ② 非表示/再表示 |
| POST | `/api/v1/jobs/{id}/deletion-plans` | ③④のpreflight: 何が消えるか（件数・名前）と拒否理由。副作用なし。短命token |
| POST | `/api/v1/jobs/{id}/deletions` | ③または④の実行。`mode: "history" | "history_and_artifacts"`、plan token、If-Match、Idempotency-Key |

B8のsetupと同じ「plan → 明示承認 → 実行」の二段階にし、preflightで見せた内容と実行時の状態が違えば409で止める。

## 5. 互換性・移行

- v7 migrationは追加のみ（②の表、③の記録表）。v6のbackupは既存の仕組みで残る。旧buildはv7を開けない（既存方針どおり一方向）。
- 既定起動（旧GUI/CLI）はcontrol DBを作らない・触らない、のまま。
- 旧GUIの履歴（`utteran_gui/history.py`、core job manifest）は**読み取り専用で参照**し、本設計の削除対象にしない（束F）。

## 6. 試験計画（実装時）

- データ消失防止: 元音声が残る／他Jobのディレクトリが残る／marker不一致・symlink・想定外ファイル・編集済み成果物では何も消えない／
  旧snapshotのJobは④を拒否／途中失敗で履歴が残る／同keyの再送で二重に消えない／実行中Jobは409。
- 非表示: 再表示で元どおり、キュー表示・B9 `/sync`に影響しない、cursor pagingの整合。
- 墓標化: 削除後のDBファイルとWALに入力path・Prompt・用語集のsentinelが残らない（backupには残ることを文書と一致させる）。
- Windows: 開いているファイル（Explorerのプレビュー、エディタ）の削除失敗、長いpath、OneDrive配下 → Codex受入。

## 7. owner判断をお願いした点（2026-11-16に確定済み。§0）

1. ②を先行実装してよいか（追記専用の表を足す**v7 migration**を伴う。既存行は無変更）。
2. ③は**墓標化（③-a）**でよいか、行の物理削除（③-b）を必須とするか。
3. ④で、想定外のファイルがあるディレクトリ・編集された成果物を「既定は拒否」とする方針でよいか。
4. 「完全消去」とは表現しない（backup・WAL・ごみ箱に残り得る）方針でよいか。
