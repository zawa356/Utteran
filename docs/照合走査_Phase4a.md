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
