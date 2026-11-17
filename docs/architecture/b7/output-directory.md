# B7 — ジョブ固有の出力ディレクトリと同一stem命名

更新: 2026-11-16 JST（開発版`0.1.54`・未リリース、束C1）。プログラム全体は[../program/roadmap-2026-11-16.md](../program/roadmap-2026-11-16.md)。

> v1（B7→B3）経路だけの仕様。**旧GUI・CLIの出力命名（入力stem＋衝突時の連番）は変更していない。**
> Windows実機（長いpath、OneDrive配下、大小文字、Explorerでの見え方）は未確認。

## 1. 仕様

```
<ユーザーが選んだ出力先>/
  <入力stem>_<YYYYMMDD-HHMMSS>_<Job IDの先頭8桁>/     ← ジョブ1件に1つ
    <同じ名前>.srt / .vtt / .json / .txt / .md          ← 成果物は全部ディレクトリと同一stem
    .utteran-job                                        ← 所有marker（1行、Job ID）
```

- **時刻は投入時刻**（ローカル時刻）。`JobSnapshot.output_stamp`としてbatchごとに1回だけ凍結する。名前は凍結値とJob IDだけの
  関数（`utteran_contracts.output_naming.job_output_name`、ファイルシステム非依存）なので、何回目の試行でも同じになる。
- `result.srt`のような汎用名は使わない。別ジョブと成果物名が一致することはない。
- **`retry`**（同じsnapshotから新しいJob）: 同じ`output_stamp`・別のJob ID → **別のディレクトリ**。失敗した元ジョブの途中成果物には触れない。
  coreのASR等の段階cacheは従来どおり再利用される（出力先はexport段階のhashにだけ入る）。
  **`rerun`**（現在設定から別Job）: 新しいsnapshot・新しい時刻・別ディレクトリ。
- 公開DTOは無変更（pathは公開しない）。成果物の参照・削除APIは束C2。

## 1.1 短縮IDが一致したとき（PR #51レビュー修正、版0.1.54据置）

名前に使うJob IDの先頭8桁は、それだけでは一意でない。同じ入力名・同じ投入秒のJobが2つあり、Job IDの先頭8桁も一致すると
（約43億分の1。ただし起きたときの影響はデータ保護に関わる）、2つのJobが同じディレクトリ名を計画する。

- 修正前: 後から来たJobは他Jobのmarkerを見て`blocked`（`output_unavailable`）。**既存成果物は守られていたが、そのJobは
  再検査しても永久に進めなかった。**
- 修正後（`resolve_output`）: ID部分を**8 → 12 → 16 → 24 → 32桁（全体）**の順に伸ばし、最初に確保できたディレクトリを使う。
  取られているディレクトリの中身は読まない・変えない・消さない。全ての形が取られているときだけ`blocked`。
- **試行間の安定性**: まず「このJobのmarkerがあるディレクトリ」を全ての長さから探し、あればそれを使う。短い名前のディレクトリが
  後で消えても、既に確保した場所を使い続ける（途中から別の場所へ書かない）。
- 「同名で中身があるがmarkerが無い」ディレクトリ（ユーザーが作った等）も同じ扱いで、**そこへは書かず次の長さへ進む**
  （修正前は`blocked`）。markerの競合書込（別processが先に書いた）は「取られている」と判定する。
- 長いID形式でもpath予算（240文字）を守る。収まらない長さは候補にしない。
- B3の検査（`output_dir`の名前＝`output_stem`）と完了検証はそのまま通る（どの長さでもディレクトリ名とstemは同一）。

retryの意味（Design Freezeは「同一Job」、B7実装は`retry_of`付きの新Job）の差異と解決案は
[../program/retry-semantics.md](../program/retry-semantics.md)（owner判断待ち、未実装）。

## 2. Windowsで安全な名前

`safe_stem`（どのOSでも同じ規則）: NFC正規化、`< > : " / \ | ? *`と制御文字を`_`へ、前後の空白と`.`を除去、空なら`input`、
stemは最大80文字。末尾に必ず`_<時刻>_<ID>`が付くので、`CON`・`NUL`等の予約デバイス名も無害になり、同名・同時刻・
大小文字だけが違う入力でも衝突しない。

長さ: `<出力先>/<名前>/<名前>.<拡張子>`が**240文字**（MAX_PATH 260に対する余裕）に収まるようstemを短くする。
stemを8文字まで縮めても収まらない出力先は、**投入時に422** `admission_invalid` / `/files/N/output_dir` `output_path_too_long`で拒否する
（黙って別の場所へ書かない）。

## 3. 上書きしない保証

| 層 | 検査 |
|---|---|
| B7 dispatcher `claim_output_dir`（attempt開始前） | ディレクトリを新規作成してmarkerを書く。既存なら**同じJob IDのmarker**があるときだけ継続。中身があるのにmarkerが無い／別ジョブのmarker／symlink／ファイル／親が無い → `blocked`（`output_unavailable`）。workerは起動しない |
| B3 `explicit_config` | `output_stem`があるとき、`output_dir`の名前がそのstemと完全一致しなければ`invalid_context`。共有ディレクトリへ名前指定で書く要求は成立しない |
| core `export_all(stem=...)` | 明示stemのときだけ、その名前で書く（自分のディレクトリ内の自分の途中成果物は置き換える）。stem無し（CLI・旧GUI・旧snapshot）は従来どおり連番で**上書きしない** |
| GUI `verify_result` | 完了時、成果物がそのディレクトリ直下にあり、stemが一致することを確認。違えば`artifact_invalid`で失敗 |

ユーザーの出力先に元からあるファイル（例: 旧命名の`a.srt`）は対象にならない（試験で内容不変を確認）。

## 4. 互換性

- **0.1.54より前に受理されたsnapshot**（`output_stamp`なし）は、受理時の意味のまま: 出力先へ直接、coreの命名で書く。
- stemは**core `Config`に入れていない**（`Config`にfieldを足すと旧CLIの設定出力とresume hashが変わるため。旧CLIのgolden試験で検出）。
  private IPC `WorkerExecutionContext.output_stem`と`run_pipeline(output_stem=...)`で渡す。`worker.schema.json`再生成。
- 新GUI→旧workerは未知fieldで拒否（fail closed）。旧GUI→新workerは`output_stem`なしで従来動作。
- **downgrade不可**: 0.1.54以降に作ったsnapshotは`output_stamp`を持ち、0.1.53以前のbuildは未知fieldとして読めない（一方向）。
- markerは成果物ではない（履歴・成果物削除で対象ディレクトリを検証するために束C2でも使う）。

## 5. 試験

`tests/test_job_output_dir.py`（35件。うち短縮ID衝突の再現と修正確認4件）: 命名（禁止文字・予約名・NFC・長さ・path予算・不正な時刻/ID）、投入時の凍結と決定性、
同名入力2件の分離、収まらない出力先の422、旧snapshotの互換、marker（新規・自分・他人・中身あり・空・symlink・ファイル）、
他人のディレクトリではdispatcherが`blocked`になりrunnerが呼ばれないこと、実B3＋core exportで5形式が同一stem・ユーザーの既存ファイル不変、
retryが別ディレクトリで元の途中成果物を残すこと、workerの名前不一致拒否、完了検証のstem不一致拒否、coreの既定連番が不変であること。
