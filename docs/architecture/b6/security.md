# security / privacy

## private / public の分離

- private: `operation_private.body`（SubmitOperationの`private_payload`）、`attempt_private`
  （owner nonce、owner/child PIDとcreation identity）、installation secret。
  取得APIは`get_private()`（`PrivateOperation`）のみ。`LeaseGrant`のfencing tokenはownerだけに返す。
- public: `OperationView`、`QueueView`、`EventEnvelope`/`EventPage`、`ControlSnapshot`、
  `RecoveryReport`。すべてtyped columnからfield単位で構築し、private列をjoinしない。
- replay bodyはpublic viewのみ。request digestはHMAC（平文hashなし）。

テストではPrompt/path風の合成sentinelとnonceを使い、private table以外の全table dump、
snapshot、event、view、例外str/repr、log record、schema、例へ出ないことをassertする。

## 認証情報

private payloadのkeyが`token|secret|password|api_key|authorization|cookie|credential|bearer`
（大小無視）に一致する場合、またはHugging Face token形式の値を含む場合は
`private_payload_forbidden`で拒否し、何も保存しない。HF/API key/session tokenの値は永続化しない。
これは誤投入防止の網であり、任意の秘密値検出を保証するものではない。

## 例外・ログ

`ControlError`は固定codeのみを持ち、`from None`で元例外を連鎖させない。SQL文、SQLite message、
path、入力値、Verifier例外文を含めない。`utteran_control`はloggingを行わない。
DTOの`repr`はB1 Contractの規則で値を出さない。

## OS権限と限界

- POSIX: directory 0700、DB 0600、symlink/他uid所有を拒否。root権限は回避できない。
- Windows: 継承ACLに依存し、独自DACLは設定しない。安全とは断言せず、実機ACL受入は未完了。
- portable/共有folderへのコピー・同期では権限が保たれない可能性がある。backupも同じprivate情報を含む。
- **DBは暗号化していない**。private payloadとnonceは平文で保存される。保持は無期限（GCなし）。
- control DBのsecretはB5と別で、B5 secret/installationを読まない。
