# 互換性と明示copy/import計画

旧CLI config.toml/env/.envとGuiSettingsは引き続き正本。新リソースは独立し、旧job・stage hash・
resume・出力・profile/manifestを書き換えない。GUIは軽量contracts/domainのみimportする。
B4 resolveは独立の明示入口のまま。B5はinventory refresh・smoke・processを呼ばない。

B2の空Prompt保持に対してB5 previewのみnullへ正規化する差分を明記した。
B5 settingsをworkerへ渡す統合はB7でhash/adapter versionをレビューする。旧jobを再計算しない。
GenAI/glossary等は保存できても現worker未対応である。Prompt効果保証はIssue #1実測待ち。

## 明示import設計（実装しない）

1. 利用者がsource kind/versionを指定して明示previewを要求する。
2. core側で現行CLI優先順位`CLI > env > .env > TOML > defaults`を評価し、秘密/tokenと
   GUI表示設定・path等を除いたProcessingSettings候補をprivate responseに返す。
3. 利用者が候補とsourceを確認した場合だけ、通常POSTと新idempotency keyでcopy作成する。
4. source kind/version/timestamp/provenanceは将来のprivate auditに記録し、公開logへ出さない。
5. GUI default_profileからfixed policyを提案する場合はB4で対象candidateの実在・readyを明示確認。
   未確認はunknownとして選択確認を求める。profile名からcandidateを捏造しない。

B5にはimport endpointや初回自動作成を設けない。既存sourceを更新しない。
B6 operation store/outbox、B7 atomic submit、B8 setup、B9 events、U1 UI接続は未実装。

0.1.34へpatch更新し、uv.lockはroot versionだけ変更。`__version__`は両packageとも
importlib.metadataから読む既存方式なのでリテラル更新は不要。wheel/editableを更新して一致検証する。
lock全体hash変更による旧profile dependency_lock_changedは既知課題のまま。
manifest/venv改変・再sync・fingerprint検査回避をしない。
