# B8 security / privacy

- 認証・Host/Origin・CSRF・body上限はB5 v1 sub-appの単一middleware（新mountなし）。
- **credential値は扱わない**: observerは`TokenStore.status().configured`（有無のみ）を読む。値はDB・plan・
  approval・journal・応答に入らない。tokenの子processへの受け渡しは既存`CliAdapter.environment()`の経路のみ。
- **同意を偽装しない**: `required_agreements`は利用者が`accepted_agreement_ids`で明示しない限り承認できない
  （409 `agreement_required`）。B8はHugging Face上の同意を代行しない。同意未了は実行時preflightで
  `waiting_user(agreement_required)`。
- 子process: 固定argv、`shell=False`、plan値は検証済みenum/catalog refのみ。stdout/stderrは読み捨て、
  credential preflightの最終JSON行（64 KiB上限）から`access`だけを使う。raw出力・path・argvは保存・返却しない。
- 公開DTOは固定語彙とID・時刻・件数のみ。step journal（PID・exit code）はprivate。
- sentinel test: token形の合成値を子processの出力へ流し、DB dump・HTTP応答・plan本文に出ないことを確認。
- private DBはB6/B7同様に暗号化なし・無期限保持（未解決ゲート）。Windows ACLは未検証。
