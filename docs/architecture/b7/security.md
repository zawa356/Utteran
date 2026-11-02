# HTTP・セキュリティ・プライバシー

## HTTP境界

- B5 `create_settings_app`のmiddlewareを共有（二重mount・origin/Host bypassなし）: 無認証401、
  不正Host 403、cross-origin 403、状態変更（POST/PUT/**PATCH**/DELETE）でOriginもsession headerもない
  cookie-only要求は403（CSRF）。
- 固定エラー: 未登録404、契約違反422、If-Match欠落428、`*`/不正形式400、revision競合412、
  semantic/idempotency/resolution競合409、store busy/full/readonly・runtime非活性503、payload 413。
  error bodyは`jobs.<code>`とJSON pointer issueのみ。path・Prompt・例外文は返さない。
- body: B5 `_body`（`application/json`必須、8 MiB上限、重複key・invalid UTF-8・NaN拒否）、
  store digest対象は256 KiB上限（413）。batch 100 file上限。
- Idempotency-Key: `[A-Za-z0-9_.-]{16,128}`必須（B6の可視ASCII制約の部分集合）。
- B9 `/api/v1/events`・`/sync`は未登録（404維持）。
- 一覧cursorは閲覧session credentialのSHA-256・snapshot・位置・page sizeにHMAC束縛し、private値を含まない。
  materialized snapshotは公開Job DTOのみ（プロセス内、TTL/容量上限あり）。
- `503 submission_in_progress`・`cursor_expired`等のbodyは固定codeのみ（token・path・本文なし）。

## Private dataの所在

| 値 | 保存先 | 公開/ログ/Outbox |
|---|---|---|
| Prompt・glossary・overrides・effective settings | `job_snapshots`（private） | 出さない |
| 入力/出力path、モデルdir | `job_snapshots` | 出さない（入力はHMAC `in_*`のみ） |
| WorkerRequest・argv・env | メモリのみ（B3） | 出さない |
| owner nonce・PID・start identity | `attempt_private` | 出さない |
| resolution token | メモリのみ | 応答本人のみ |
| artifact/manifest digest | `job_results.evidence` | 出さない（件数のみ） |

テスト: snapshot/attempt_private以外の全表（operations、history、outbox、queue_items、全replay表）、
公開Job/JobList/Event、ログにsentinel・tmp path・モデルdir・ファイル名が含まれないことを検証。

## 入力path

UNC/ネットワーク、symlink/junction/reparse point成分、相対path、NUL、非通常file、空fileを拒否。
content SHA-256で同一性を保証（inode/mtimeのみに依存しない）。Windowsのreparse判定は
`st_file_attributes`による（CI実行、実機未検証）。

## 未解決ゲート

- private DBは**暗号化なし・無期限保持**、GC/完全削除・バックアップ保護方針は未策定。
  backupファイルにも同じprivate値が含まれる。
- Windows ACL（POSIXは0700/0600）・portable・installer・PyInstaller同梱は未検証。
- ベンチマーク・GPU・長時間推論は未測定（unknown）。
