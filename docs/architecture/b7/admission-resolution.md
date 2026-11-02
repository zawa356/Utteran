# 受理前Validation・Resolution token・freeze proof

## `POST /api/v1/execution-resolutions`（Idempotency-Key必須）

1. `EditingSession`をPydantic strict検証（file 1件=Job 1件、`client_file_id`重複禁止、empty拒否、
   folder再列挙なし）。B7上限は1 batch **100 file**（`batch_too_large`）。
2. B5 Repositoryから**指定revision**のPreset/Policyを読み、B5 `preview()`で
   `preset.settings → common_overrides → file.overrides`のleaf継承を適用。issueがあれば全件422。
   `worker_support="unsupported"`のwarning（backend、diarization、glossary、extra args等）も422
   `worker_unsupported`。**`unverified`はreadyの証拠として扱わない。**
3. 入力identity（全file）: 絶対path、NUL禁止、UNC/`//`ネットワークpath拒否、各path成分をlstatし
   symlink/reparse point拒否、通常file、size>0。content SHA-256を計算し、前後のstatが一致しなければ
   `input_changed`。出力dirは絶対・実在dir・link成分なし。issue pathはJSON pointer（`/files/1/input_path`）のみで、
   実pathは返さない。
4. 有効設定ごとに`ResolutionProvider.resolve()`。既定はprofile bridge
   （`python -I -B -m utteran.capabilities.bridge resolve`、stdinでprivate入力）。Policyの
   `allowed_profile_ids`（空なら cpu→cuda→intel→vulkan）を順に問い合わせ、最初のselectionを採用。
   **profile横断の順位付けは主張しない**（B4 inventoryはprofile単位）。
5. selectionなし→409 `execution_unavailable`（`no_runnable_candidate` / `fixed_candidate_unavailable` 等）。
   selectedがB3 worker対応外（recipe、path、diarization、glossary、args、ローカルモデルdir不在、非ready）も409。
6. 成功時は短命tokenと`Resolution`（201）。公開viewにsettings・path・model dirを含めない。

## Resolution token

- 形式 `v1.<resolution 32hex>.<expiry ms 13桁>.<HMAC-SHA256 64hex>`。MACは
  プロセスごとの乱数鍵で `id|expiry|session binding` に対して計算。**settings/Prompt/pathを含まない**。
- session binding = SHA-256(提示されたlocal session credential)。別sessionは`resolution_invalid`。
- TTL **5分**。期限切れ`resolution_expired`、再利用`resolution_consumed`、改ざん・任意文字列・
  base64 JSON は`resolution_invalid`（409）またはschema違反422。
- 記録はメモリのみ（最大256件）。**GUI再起動で全token失効**（明示仕様）。
- tokenは単一使用。提出中はreserve（予約者=要求scope+digestのHMACを記録）、commit成功でconsume、
  失敗でrelease。同key・同bodyの並行再送は409にせずleaderの結果を待つ（有界、期限で503
  `submission_in_progress`）。別keyでの再利用は`resolution_consumed`、別sessionは`resolution_invalid`。
  tokenはプロセス内のため、別OSプロセスからは`resolution_invalid`になる（二重登録は起きない）。

## 提出時のfreeze proof（`POST /api/v1/jobs`）

1. **Idempotency replayを最初に照合**（token・入力・CAS検査より前）。commit後の応答喪失・再起動後でも
   同じJobBatch/Location/ETagを返す。同key別bodyは409。
2. tokenを同じ`EditingSession` digestでreserve（編集差異は`resolution_stale`）。
3. B5 revisionを再読し、digestがtoken記録と一致すること。B5 previewを再計算し、有効設定がfrozen specと一致。
4. 全fileの入力identityを再取得し完全一致。
5. 環境を再観測し、**凍結したcandidate_idが引き続きeligible・ready・同一fingerprint・同一モデルdir**であること。
   新しい候補への再選択・fixed→別候補へのsilent fallbackは行わない。
6. 1件でも差異があれば409 `resolution_stale`、DBへは何も作らない。

## B5 DBとB6/B7 DBの関係（TOCTOU）

二つのSQLiteを跨ぐ原子的COMMITは**主張しない**。B5 revisionはimmutableなので、
(a) 解決時にrevision内容を読みdigestを記録、(b) 提出時に同revisionを再読してdigest照合、
(c) Policy内容をsnapshotへコピー凍結、の3段で整合を担保する。B5側で新revisionが作られても、
参照revisionが同じ限り受理内容は変わらない（テストあり）。inventoryの可変情報はfingerprint再照合で検出。
