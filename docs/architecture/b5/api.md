# opt-in v1 API / Phase A照合

`create_app(session_key, repo_root=..., enable_v1_settings=True,
v1_authority="127.0.0.1:8765")`。実際にbindしたloopback authorityを明示すること。
通常起動はFalseで、CLI flag/frontend操作はまだない。test用v1_store_path以外は正式data rootを使う。
`/api/v1`の独立subappはGUIのsession key/cookieとsecurity headers/no-storeを継承し、追加で
exact Host、same-origin、Originなしmutationの専用session headerを要求する。

| endpoint | 動作 |
|---|---|
| GET /api/v1/presets, /api/v1/execution-policies | snapshot items/next_cursor/revision、collection ETag |
| POST 上記collection | Idempotency-Key必須、201、Location、ETag、restart後replay |
| GET /api/v1/presets/{id}, /api/v1/execution-policies/{id} | latest / ?revision=n、ETag |
| PUT 上記resource | If-Match必須、full write DTO、200、revision追加 |
| DELETE 上記resource | If-Match必須、tombstone、204 |
| POST /api/v1/validations | EditingSession、Idempotency-Key、200 ValidationResult / 422 Error |

If-Matchは`"<revision>"`、wildcard/weak/multiple値は禁止。missing 428、形不正400、CAS mismatch412。
POST keyはASCII `[A-Za-z0-9_.-]{16,128}`に限定する（Phase Aのstring lengthを具体化）。
JSON UTF-8/application-json、8 MiB（stream中にも検査）。重複JSON key、NaN、不正JSONは400。
未知field/型422、未知schemaはschema_version_unsupported 422、401 session、403 origin/host、
404 resource、409 idempotency、413容量、503 store。ErrorはB1 Error shapeと固定reason/pathのみ。
参照不明revisionのvalidationは422 reference_unresolved。resource GET自体は404。

## Phase Aとの変更・生成元

B1/B3/B4 artifactを変更しない。B5の生成元は`utteran_contracts.preview`、生成物は
`preview.schema.json`。5型をPhase A openapi.yamlにも同期し、同一性テストで検査する。
既存Phase A文書は設計履歴として残し、この差分表で実装範囲を明示する。

| 提案との差分 | 理由 / 移行 |
|---|---|
| Provenanceにinput_source/input_status必須追加 | 空Prompt正規化でも元の適用元と入力状態を消さない。preview clientは追加fieldを読む |
| ValidationResultにpreset_ref/policy_ref必須追加 | 参照immutable revisionを応答に残す |
| ValidatedFileにwarnings/worker_support必須追加 | 保存可能性と実行対応/qualityを区別。readyを意味しない |
| PresetList/PolicyList revision >=1 | 空一覧の初期revision=1。既存resource revisionと区別 |
| semantic不正は422 Error | Phase A共通HTTP規約に従う。pure関数ではvalid=falseの全file結果を取得可能 |
| previewにETagを返さない | previewは可変resourceではなく、同keyのimmutable replay結果。resource revisionを捏造しない |
| 安全に分類できないExpert引数は常に拒否 | registry/tainted実行が未提供。無断passthroughをしない |
| cursorは履歴保持中無期限 | 古いsnapshotを保持できるため期限切れを捏造しない。invalid400、失効409 |

これらは未接続のopt-in契約への明示追加。既存legacy API/B1 wire shapeを変更しない。
将来clientはこのB5 artifactから生成し、Phase Aの旧preview shapeを固定実装しない。
既存CLI/GUIからの自動migrationは不要。execution-resolutions/token/job submitはB7。

```console
python -m utteran_contracts.preview_schema --output docs/architecture/b5/preview.schema.json --check
```
