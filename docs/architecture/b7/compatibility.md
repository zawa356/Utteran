# 互換性・cutover / rollback

## ブランチ・基点

- 推奨名は`feat/b7-immutable-jobs-queue`だが、本Claude Code環境では作業ブランチが
  **`claude/affectionate-galileo-4dhfpo`に固定**されているため、そのブランチで開発しDraft PR（base `main`）とした。
- 基点HEAD: `3fd187dff64e83291253bd0d0bf4ba74f043664a`（main）。rebase・force pushなし。

## 既定挙動（不変）

- `create_app()`既定・`utteran_gui.app`: control DBを作らず、`utteran_control`・B7 module・coreを
  importしない（subprocessテスト）。v1 Job routeは存在しない（404/405）。
- 旧`/api/*`、旧`JobManager`/`OperationQueue`、旧SSE、Setup wizard、CLI、B3 worker client既定引数、
  B4 bridge `recipes`/`refresh`、B5 API、B6 `ControlStore`(v2) の挙動は不変（既存テスト全通過）。
- 変更点: B5 sub-appの「Origin必須」判定にPATCHを追加（B5にPATCH routeは無く、挙動差なし）。
  B6 `ReasonCode`に`user_reorder`を追加（B6 schema再生成、既存値不変）。

## Opt-in（実験モード）

`create_app(..., enable_v1_settings=True, v1_authority=..., enable_v1_jobs=True)`。
`enable_v1_jobs`単独はValueError。jobs routeは**B5と同じsub-app**に登録し、`/api/v1`のmountは1個。
本番（`app.py`）での自動有効化は行わない（別承認）。

## Cutover（`JobRuntime.activate`）

1. 旧`OperationQueue`に waiting/running が**0件**であることを確認し、Runtimeごとの予測不能token
   （`secrets.token_hex(32)`）で`close_for`（単一writer、非再入）。1件でもあれば`legacy_busy`、
   既に別Runtimeが所有していれば`legacy_queue_owned`で拒否。以降の失敗（store open・backup・recover・
   dispatcher起動）では自分が取得したtokenだけを解放する（PR #32 P2-1）。以後、旧の投入route（`/api/jobs`、`/api/wizard/jobs`、
   `/api/models/actions`）は409。旧queueの読み取り・cancelは可能。
2. control DBが既存ならSQLite online backup（`operations-pre-v1-<UTC>.sqlite3`、0600）を作成。
3. `JobControlStore`で開く（v1/v2はv3へmigration、未知versionは拒否して旧queueを再開）。
4. `recover(OsProcessVerifier)` → queue paused・waiting held。
5. dispatcher開始。利用者は明示的にrelease・`PATCH /queue/control running`する必要がある。

旧queueの履歴・job・historyを「復旧できた」ことにする自動移行はしない。

## Rollback（`JobRuntime.deactivate`）

1. dispatcher停止（新規dispatchなし、実行中は`cancelling`→cooperative cancel→終了待ち）。
2. activeまたはunknownのleaseが残る場合は`active_attempt`で**拒否**し、旧queueは閉じたまま。
3. DB backup（`operations-rollback-<UTC>.sqlite3`）→ 所有tokenで旧queueを`reopen`（一致しなければ
   `ownership_lost`で拒否し開かない）。DBは削除しない。

排他の範囲: 旧Queue排他は**同一Pythonプロセス内**。別OSプロセス間の共有DBの実行権は、SQLiteの単一
heavy slot（部分UNIQUE index＋lease PK）とfencing tokenが保証する。

制約: v3 DBは旧B6コード（0.1.35）では開けない（`store_schema_unsupported`）。旧版は新Jobを
表示できないため、旧版バイナリへの戻しは**DBを残したまま旧GUI/APIのみ使用**する形に限られる。
ユーザー資産（DB・backup・出力）を消して戻すことはしない。

## 依存

新規依存なし。core（bridge）以外のGUI側はcontracts/control/settingsのみimport。
