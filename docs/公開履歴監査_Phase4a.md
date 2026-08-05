# Phase 4a 公開履歴監査

## 結論

- **公開ref内の秘密値候補は0件**。現時点で失効を急ぐべきトークン／APIキーは
  検出されなかった。最終再走査で、ローカルの到達不能blobにscannerの回帰testが意図的に
  使った秘密値形式1件を検出した。値は表示せずtestの前後関係を確認し、実credentialではないと
  判定した。このobjectはどのrefからも到達できず、削除やGCは実施していない。
- メールアドレス形式は、コミットメッセージ10件と過去差分7件を検出した。
- ユーザー固有の絶対パス候補は、`AISTATE.md`を導入したコミットの差分に2件、同内容を持つ
  到達不能blobに2件を検出した。現在の`AISTATE.md`では一般化した表記へ是正した。
- メディアファイル、`.env`、実文字起こし成果物がGitのファイルパスとして登録された形跡は
  検出されなかった。成果物候補として抽出された追跡パスは、受入ケース定義とその説明文書だけで、
  文字起こし本文を含む成果物ではなかった。
- 検出値そのものは、本書、標準出力、コミット対象ファイルのいずれにも記録していない。

履歴の書き換え、force push、ブランチ／タグの削除、pushは実施していない。

## 走査範囲と方法

2026-08-05に`origin`をfetchした後、`tools/public_history_scan.py`で以下を走査した。

| 対象 | 件数／方法 |
|---|---|
| refs | 20（ローカルbranch、remote-tracking branch、tag、作業支援refを含む） |
| 全refから到達可能なcommit | 111 |
| Git object | 1,074（到達可能1,052、到達不能22） |
| 到達不能object | blob 9、tree 13 |
| commit message | 到達可能／到達不能commit objectを直接走査 |
| 差分 | 到達可能111 commitをroot commitを含めて`git show --unified=0`相当で走査 |
| file path/content | 各commit tree、全blob、到達不能tree/blobを走査 |
| refs | `git for-each-ref`で名称を走査 |

検出器はメールアドレス正規表現、Hugging Face／GitHub／AWSキー形式、秘密鍵ヘッダー、
ユーザー絶対パス、音声・動画拡張子、環境ファイル、文字起こし／受入成果物候補を対象とする。
既知のダミー値は検出したうえで非blockingの`test-placeholder`へ分類する。
gitleaksはローカルに存在しなかったため使用していない。

完全な機械可読結果は`output/phase4a-public-history-scan.json`へ出力した。`output/`はGit除外であり、
このJSONも一致値を含まず、分類、object/commit、path、件数だけを保持する。

## 検出結果（値は省略）

### 秘密値

初回走査では0件。`hf_`形式の一致はすべて、READMEやテストが意図して持つ
ダミー値として分類された。最終再走査では上記の到達不能test blob 1件が形式検出に追加されたが、
公開refに秘密値候補はない。

### メールアドレス形式

コミットメッセージ（各1件）:

- `2eb807827982d9b413c2ac4d9b1bb2883e02758f`
- `3fdc8df1ae1bdb93bca539c00da6cb37dcfbdff3`
- `530b38de73bc79744cece58fde18cd17f3a9089e`
- `546a3db3c9abc5aad11d51ea4e2853e3a9b4d082`
- `6546a9d1652356671b0fb85bc46392c269abf0fe`
- `7e0ff299dc8b8e5b763bb2976e7e7c4e936f6d9a`
- `92c6d0d3230f8b90363e62c735993980a94e8502`
- `a1a368c777d8ad726c2fa06ff5c041a5b03f4f20`
- `c5f0d8f6a89df0a69fd6eb543e5bf9c4129b9a0d`
- `d51ad21800d63e7c4b25d5f61da14819caf76818`

過去差分:

| commit | path | 件数 |
|---|---|---:|
| `83a4b29029d226ac28574e0e3f67c3adb6eef70c` | `src/utteran/cli.py` | 2 |
| `54fa46dd47ff7006a54d74580511771d2373bcc3` | `src/utteran/cli.py` | 1 |
| `a06713239c5caa74ef4720a8f857e63f90e6cecc` | `src/utteran/cli.py` | 1 |
| `6546a9d1652356671b0fb85bc46392c269abf0fe` | `tests/test_native.py` | 1 |
| `979c7ca8e1e72833fa49661ff5dfb45655090acf` | `tests/test_acceptance_scenarios.py` | 1 |
| `ff21898a992a428a9fb74a1231e6e822d4ed5a9f` | `tests/test_jobs.py` | 1 |

メール形式は機械的に全件抽出しており、公開連絡先やテスト値かどうかで除外していない。

### ユーザー固有パス候補

| object/commit | path | 件数 | 状態 |
|---|---|---:|---|
| `83a4b29029d226ac28574e0e3f67c3adb6eef70c` | `AISTATE.md`の導入差分 | 2 | 履歴に残存 |
| `4a0aef50e31c5ac8a5bf3fb2dcc532fb3c13f61a` | 到達不能blobの`AISTATE.md` | 2 | 到達不能objectに残存 |

テスト用の一般名を使う絶対パスは検出済みダミー値として別分類した。

### GitHub側

GitHub CLIの認証済みread-only API取得で確認した。

| 対象 | 結果 |
|---|---|
| Issues / Pull Requests | 0件 |
| Issue comments / review comments | 0件 / 0件 |
| Actions runs / logs | 0件。実行ログなし |
| Releases | 1件。本文・名称・tagを走査し検出0件、asset 0件 |
| Wiki | wiki repositoryなし |
| Discussions | 無効 |

## 対処方針

現作業では、現在の追跡ファイルからユーザー固有パスを除去し、`.gitignore`回帰テストと
現在treeのblocking走査をCIへ追加する。既知の過去履歴は監査結果として残す。

過去履歴への選択肢は次のとおり。実施判断は利用者に委ねる。

1. **履歴を維持する（現作業の既定）**: cloneや既存tagを壊さない。過去のメール形式と絶対パス候補は
   公開履歴に残る。
2. **履歴を書き換える**: `git filter-repo`等で対象を除去し、全branch/tagをforce pushする。
   既存clone、fork、PR参照、公開済みrelease/tagとの整合を壊すため、関係者への告知と再cloneが必要。
   到達不能objectはローカルGC、GitHub側はsupportへのcache削除相談が別途必要になりうる。
3. **リポジトリを非公開化する**: 今後の露出は抑えられるが、既存cloneやcacheからの回収はできない。

秘密値は公開refから検出されなかったため、トークン失効を先行すべき事象はない。

## Phase 4a実装後の最終再走査

commit `a36b917`までを含め2026-08-05に再走査した。refs 20、commit 115、Git object 1,144
（到達可能1,116、到達不能blob 15／tree 13）、115差分を走査した。過去の同一パス表記を
各commitのblobごとに数えるため、生のfinding数は記述上の論理的な件数より多い。

- 現在tree: 119追跡file、44 finding、blocking 0
- 公開ref: 新たな秘密値、media、`.env`、実transcriptなし
- 既知の個人情報候補: コミットメッセージのemail形式と、過去の`AISTATE.md`の
  user絶対path。現在treeからは除去済み
- 到達不能object: 上記の意図的なscanner test値形式1件。公開refに未含有

機械可読結果は`output/public-history-scan-final.json`に保存した。これも検出値を含まず、
`output/`はGit対象外である。
