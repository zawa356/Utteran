# B8/B9 互換性・運用制約

## ブランチとPR（指示書§1の制約）

指示書は`feat/b8-setup-plan-approval-journal`（base main）と`feat/b9-events-sync-diagnostics`（base B8）の
2本のstacked Draft PRを推奨する。本セッションのpush先は環境により**`claude/funny-cori-9lnbpr`の1本に固定**
されており、別名のremote branchを作成・pushできない。履歴の書換えや2本のPRの偽装は行わず、
**B8 commit系列 → B9 commit系列を同一ブランチ上で分離し、単一のDraft PRとして提出**する。

レビュー境界: B8は`chore(b8)`/`feat(b8)`/`test(b8)`/`docs(b8)`のcommit、B9は`*(b9)`のcommit。
B8最終commit（version 0.1.37）とB9最終commit（0.1.38）はPR本文に記載する。

## 既存機能

- 変更なし: 既定`create_app()`、旧`/api/*`、旧OperationQueue/JobManager/SetupWizardService、UI、CLI、B3〜B5。
- 追加のみ: `utteran_contracts.setup{,_schema}`、`utteran_control.setup_repository`、
  `utteran_gui.setup_{planner,executor,api}`、`create_app(enable_v1_setup=...)`、
  `JobRuntime(setup=SetupComponents(...))`、`JobDispatcher(setup=...)`。
- B6変更（追加的）: `ReasonCode`に`step_progress`・`reapproval_required`（store予約）、`ErrorCode`にB8コード、
  setup遷移表に`waiting_user→cancelled`・`interrupted→cancelled`。B6/B7のschema JSONを再生成（enum追加のみ）。
- B7変更（追加的）: list snapshotの要素型を一般化（Job以外の公開viewも保持可能）、dispatcherに`setup`委譲。
- `enable_v1_setup=True`は`enable_v1_jobs=True`を必須とし、Runtimeが`SetupComponents`を持たなければValueError。
- 既定起動では`utteran_control`・setup module・`utteran_contracts.setup`をimportしない（subprocessで検査）。

## 未解決・別スコープ

- whisper.cpp native build、OpenVINO IR/GenAI、既存venv rebuild/generation切替、B4 smoke/ready登録。
- profile単位のread lease（現在はheavy slot全体排他）。
- 実Windows・GPU・実ダウンロード・実smokeでの受入（ユーザー承認が必要）。
- private DBの保持期間・完全削除、Windows ACL。

## PR #33レビュー修正の互換性（2026-11-02追補）

- 同じDraft PR・同じブランチへ通常push（force push・rebaseなし）。version 0.1.38・`uv.lock`は不変。
- DB layout: v5→v6（追加表のみ、既存表・triggerは不変）。一方向: v6はB9提出時のv5コードでは開けない。
- `ProcessChildRunner`の公開振る舞い（`ChildResult`、`shell=False`、`on_spawn`、process supervisorの
  popen_factory）は維持。新しい例外`ChildOutputError`は終了確認済みのreader失敗を表す。
