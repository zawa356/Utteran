# Security / Privacy

- loopback authorityと実portをopt-in時に必須指定。任意外部hostでは起動しない。
- cookieは既存launchのHttpOnly/SameSite=strict、専用headerは既存名。sessionをqueryで受けない。
- 全v1操作でsessionを検査。Host完全一致、Originがある場合http://bound-authority完全一致。
  Originなしmutationは専用header必須。cookieだけのcross-site mutationは403。
- 親GUIのCSP/self、no CORS、nosniff/no-referrer/no-storeを保持。新APIだけ認証を省略しない。
- raw Pydantic error/input/ctx/unknown field名/map key、SQLite path/例外を応答/logへ出さない。
  error locは登録field/固定語/数値indexへ投影。DTO reprも既存の非公開projectionを継承する。
- private effective settingsは認証されたno-store応答だけに返し、SSE/logへ送らない。
- Prompt/用語集/labels/input pathはprivate。DB/replay/backupもprivateであり暗号化なし。
  request全体は保存せず、HMACと必要なresponseだけ。keyring tokenは参照・複製しない。
- POSIX 0600/0700を設定。Windowsは既存user/portable rootのACLを継承し、厳密DACLは未検証。
- shell/外部process/network/model/legacy settingsへの副作用なし。previewの唯一の書込みは
  idempotency結果の保護保存。通常GUI起動時はDBさえ作成しない。
- rollback・履歴保持・完全消去の限界は[storage](storage.md)を参照。

合成sentinelでerror/log非漏洩をテスト。成功private response/DBへ保存した値は秘密データとして扱う。
ローカル管理者/同user processによるDB読取、disk/backupの消去保証を解決したとは主張しない。
Windows ACL/portable/WebView2/installer/実GPU受入は独立の未検証項目。
