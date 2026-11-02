# 保存と復旧

専用`resources.sqlite3`、SQLite user_version=1。正式な`DataPaths.settings_resources`は
通常`platformdirs.user_data_dir("utteran")/settings-v1/resources.sqlite3`、明示data root時は
`<UTTERAN_DATA_ROOT>/data/settings-v1/resources.sqlite3`。checkout/CWDを既定保存先にしない。
storeは最初の認証済みv1操作時に作成。旧GuiSettings/CLI configは読み書きせず、default resourceも作らない。

## テーブルとtransaction

- installation: installation UUIDとランダム32-byte HMAC secret（keyring/API tokenではない）。
- collections: preset/policy別のcollection revision。空collectionは1、mutationで+1。
- records: kind/id/revisionのPK、collection generation、deleted、private JSON。
- replays: installation/method/canonical endpoint/keyのPK、request HMAC、status/headers/body。

preset IDはpre_、policy IDはpol_とUUID hex。IDをfilesystem pathへ展開しない。
各mutationは`BEGIN IMMEDIATE`で現在revisionを読んでCASし、revision+1とcollection generationを追記。
既存行を変更せず、DELETEもbodyなしtombstoneを追記する。
latest GET/listは削除済みを返さない（GET 404）。過去の非tombstone revisionはGET可能。
削除revisionそのものは404。削除前の参照は失効しない。

SQLite既定DELETE journal、synchronous=FULL、commit時のSQLite fsyncを利用する。
DB/file作成時はPOSIX 0600、専用directoryは0700。SQLiteがjournalの権限をDBに合わせる。
Windowsではper-user/portable directoryの継承ACLに依存し、独自DACLの設定は実装していない。
folderが共有された環境でのACL適合は実機受入が必要。DBは暗号化していない。

成功createとreplay登録は同一transaction。成功previewも参照読取・計算・結果記録を同じtransactionで確定。
write timeoutは2秒、busy/locked/full/readonly/corrupt/not-a-db/IO/OS失敗を固定reasonの503へ変換。
失敗でrollbackし、DBを削除・空初期化しない。raw SQL例外はHTTP/logへ渡さない。
新規初期化失敗のfileも保持し、自動再初期化しない。同時初回初期化で未完成DBを見たcallerは
503となり得るため同keyで再送する。既存空fileは未知schemaとして拒否する。

## replayとpagination

HMAC-SHA256(canonical JSON body)でPrompt等の辞書攻撃を抑止する。JSON key順は同一bodyとして扱う。
request bodyは保存しないが、resource/preview結果にはprivate値がある。
同key/bodyは元のstatus/Location/ETag/bodyを再現。変更・削除後もcreate応答を再現する。
異bodyは409。replay照合は新しいDTO/semantic検査より先。JSONとして解析不能な要求は400。
未受理の4xx/503は記録せず、受理済み成功だけを再現する。sessionが変わってもscopeはinstallation。
現在は履歴/replayを無期限保持し、Phase Aの最低7日/active resource中の保持を満たす。

listはID辞書順、100件/page。cursorはkind/snapshot generation/last IDをHMAC署名し、
read transactionでsnapshot以下の各IDの最新revisionを選択する。途中の更新/削除/新規作成を混ぜない。
filterは未公開。snapshot履歴はpruneしないため時間でexpireしない。不正/別collection cursorは400、
現在より未来など失効snapshotは409。resource/collection/B4 inventoryのrevisionは独立。

## backup / migration / rollback

B5には破壊的migrationはない。user_version=1以外を拒否し、downgradeしない。
次schemaはbackup確認→単独writer停止→transaction内migration→検査→version更新の順を必須とする。
B6は別operation DB/別writerを使い、将来統合は明示migrationの独立レビューを要する。

backupはAPI停止後にDBと存在するhot journalを保全するか、SQLite online backup APIで整合snapshotを
privateな保護先へ作る（稼働中にDBだけcopyしない）。復旧はAPI停止→破損資産を退避→backupの
schema/integrity確認→DB/installation secret/replaysを一組で復元。再送重複を避けるためsecretだけ再生成しない。
既存資産の上書き・削除やbackup復元は利用者が対象を確認する運用で、起動時には行わない。
rollbackはv1 flagを無効化しDBを保持、旧CLI/GUIを使う。旧版へDBをdowngradeしない。

未解決: prompt/historyの保持期限と完全消去・backup消去・installation移動時のprivacy要件。
tombstoneは完全消去ではない。無断prune機能は提供しない。Refs #16。
