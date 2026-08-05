# Phase 4a 照合走査

## 結論

2026-08-05に、`input/`のfile名とWindows環境から導出した利用者固有値を、
SHA-256化した外部pattern fileでGit全履歴と照合した。全分類で一致は0件だった。

| 分類 | 一致 | 件数 | 位置 |
|---|---|---:|---|
| 組織domain | なし | 0 | — |
| emailのlocal部 | なし | 0 | — |
| 組織名 | なし | 0 | — |
| 入力file名の全体・選別した部分文字列 | なし | 0 | — |
| Windows user名 | なし | 0 | — |

値、digest、入力file名は本書、標準出力、Git追跡fileのいずれにも記録していない。

## 走査方法と範囲

- `tools/private_history_match.py build`は`input/`のfile名だけを列挙し、音声・動画の内容を開かない。
- 対象は組織domain、email local部、file名中の最長日本語列、完全file名・stem・
  完全email形式・12文字以上の要素、Windows環境のuser名である。
- pattern fileには正規化後のSHA-256、rolling hash、文字数、分類だけを保存した。
- 21 refs、116 commits、1,152 objects（到達可能1,122、到達不能blob 15/tree 15）、
  116差分、全commit message、ref名、現在119追跡fileのpathと内容を照合した。
- patternは36件、5分類。`output/phase4a-private-patterns.json`とredacted reportの
  `output/phase4a-private-match-before.json`はGit対象外である。

## 判定と履歴処理方針

既検出のemail形式は利用者固有値と一致せず、公開連絡先、開発tool trailer、または
test値と判定する。一方、過去のuser絶対pathはuser名を含みうるため、追加指示の
承認に基づき履歴をクリーンアップする。commit messageのemail形式も`<email>`へ一般化する。

## 利用者への確認事項

履歴書き換え後のforce pushとGitHub Releaseの更新は利用者作業とする。本作業でpushは行わない。

## 履歴クリーンアップ結果

`git filter-repo` 2.47.0を使い、全local branchとtagの120 commitsを書き換えた。commit messageの
email形式は`redacted-email`、Windows/Linuxのuser絶対pathのuser部は`<user>`へ一般化した。
Step 1の照合は0件だったため、利用者固有文字列の追加置換はない。

事前準備:

- `output/phase4a-history-backup-pre-rewrite/`に全21 refs・hash一覧、HEAD tree、1 releaseとtag対応を記録
- `all-refs.bundle`のverifyと復元mirror cloneの`git fsck --full`に合格
- `mirror.git`は`--mirror --no-hardlinks`で作成し、元repositoryの21 refsとobject IDが完全一致
- 別の先行backupも`output/phase4a-history-backup-20260805/`に保持

書き換え後、Git除外領域の置換rule file 2件を削除した。backup済みのローカル
`refs/codex/*` 9件はcommitでないtree refで`filter-repo`対象外だったため除去し、
`git reflog expire --expire=now --all` と `git gc --prune=now --aggressive`を実行した。

検証結果:

- 直後の8 refs、121 commits、1,146 objectsは全て到達可能で、到達不能objectは0
- 書き換え後の同じ36 hash pattern照合は5分類すべて0件
- 汎用scanに残るemail形式は、照合で非固有と判定したtest/公開用値のみ
- 置換がdummy path test 2 filesへも適用されたためbackup blobから復元し、commit
  `94c632c`のtree `2045447b3547785b7003d9342b36ca0c808abe78`が事前HEAD treeと一致
- `v0.0.1`のannotated tag objectは`231772e9...`から`bce32e7c...`、対象commitは
  `7cf578c2...`から`6f8f9a0f...`へ変更。GitHub Releaseとtagの再照合が必要
- モデル不要pytestは217 pass、ruff/format/mypy/lockfile/BOM/current-tree scanは全て合格
- 統合受入は出力経路G2の16件と公開文書G11の2件を実モデル環境で再実行し、
  18 pass / 0 fail / 0 skip（393.0秒）

pushは実施していない。安全なforce-with-leaseと新repositoryへの移行手順は
`docs/リリース手順.md`に記録した。GitHub側の到達不能objectを完全削除するには、
force push後にGitHub Supportへcache purgeを依頼する場合がある。
