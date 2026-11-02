# Phase B5 — Preset / Policy / Editing API

2026-11-02 JST、0.1.34、未マージ・未リリース。B4 PR #25のDraft/base B3/HEAD
`f6c2a9546e1ee9b780f8c1a098f7bcdfc7096314`を再確認して
`feat/b5-preset-policy-editing-api`を分岐。[Draft PR #26](https://github.com/zawa356/Utteran/pull/26)はB4へのstacked PR。mainへマージしない。
実装HEAD `f7f0f11d164e0063e7d18bc3a94d1a0d094968e9`、最終検証追補は文書のみ。

`utteran_settings`は標準libraryとB1/Pydanticのみへ依存する独立ドメイン。
GUIからcoreをimportしない。SQLite repositoryはstorage/CAS/replay、inheritanceはコピー上の
leaf適用・検証、GUI settings_apiは認証・HTTP境界を担当する。B1 DTOを再定義しない。

- [保存・復旧・migration](storage.md)
- [継承・正規化](inheritance.md)
- [APIと契約差分](api.md)
- [互換性・明示import計画](compatibility.md)
- [security/privacy](security.md)
- [検証と未検証](validation.md)
- [文書監査](docs-audit.md)
- [PR #26レビュー結果・未解決ゲート](pr26-review-validation.md)

通常のGUI/CLI起動は従来のまま。新APIは`create_app(..., enable_v1_settings=True,
v1_authority="127.0.0.1:<bound port>")`で明示的に有効化する。frontend切替、job投入、
B4 refresh/resolveの自動実行、モデル取得、profile作成はない。保存やpreview成功はreadyを意味しない。

## DoD / 後続Phaseでも必須の文書ゲート

- immutable revision、tombstone、atomic CAS、restart後replay、snapshot paginationを検証。
- B1のleaf/strict/null制約、preset→common→file、元DTO不変、provenanceを検証。
- opt-in/session/origin/host/容量/error projectionと軽量importを検証。
- README日英・変更履歴・AISTATE・Phase資料・要件/ロードマップを点検し、
  [docs-audit](docs-audit.md)のupdated/reviewed-no-change/blockedを更新する。
- テストのみでPhase完了とはしない。既知CI失敗、実機/配布未検証を隠さない。
- Draft維持、Ready化・merge・B6着手・tag/release公開はしない。
