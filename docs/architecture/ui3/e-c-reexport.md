# 束 E-C — 検証済み JSON からの ASR なし再出力

開発専用 `--next-ui-v1` のみ。対象は [#82](https://github.com/zawa356/Utteran/issues/82)（親 [束 E #77](https://github.com/zawa356/Utteran/issues/77)、UI-3 #21、Epic #18）。開発版 `0.1.69`、未リリース。
本書は E-C の正本。挙動の正本はコード（`src/utteran_contracts/transcript_export.py`、`src/utteran_gui/reexport_{files,store,service,api}.py`、`frontend/src/api/reexport.ts`、`frontend/src/domain/v1/reexport.ts`、`frontend/src/features/results/ReexportDialog.vue`）。

既定の旧 GUI、旧 `--next-ui`、CLI、B5/B6/B7 の契約・DB、E-B の作業セット、既存の出力バイトは変更していない（§10）。

## 1. 確定した UX

| 項目 | 実装 |
|---|---|
| 入口 | Expert の「結果と履歴」の詳細で、`succeeded` の最新ジョブに「再出力（ASRなし）」ボタン。D3-B2 の「現在の設定で再実行」（新しい ASR ジョブ）、再試行とは別のボタン・別の操作 |
| 対象 | 単件。サーバーが可否と理由（固定コード）を判定し、ダイアログに表示する。画面の verified 表示は根拠にしない |
| 初期値 | 元ジョブの凍結済み出力設定（`snapshot.resolved.settings.output`）の形式・話者表示・SRT BOM・改行。話者ラベルの変更は空から始める（JSON には元の変更が適用済みのため、二重に適用しない） |
| 設定 | 形式（SRT/VTT/TXT/MD/JSON、複数可）、話者表示、SRT BOM（SRT を選んだときだけ）、LF/CRLF、JSON に記録された表示上の話者ラベルごとの新しいラベル、保存先（元ジョブと同じ出力先＝元ジョブのフォルダの親／明示指定。**元ジョブのフォルダとその中は指定できない**） |
| 出力 | 保存先の中に新しい専用フォルダ `<入力名>_reexport_<YYYYMMDD-HHMMSS>_<ID>`。中身は `<同名>.<形式>` と所有 marker `.utteran-reexport`（1 行、再出力 ID） |
| 結果 | 完了・失敗（固定コード）・中断（結果不確定）、保存先パス、形式ごとのサイズ。このジョブの再出力記録の一覧（再起動後も表示） |
| 結果不明 | ダイアログは閉じられず、設定は固定。「再確認」で**同じ Idempotency-Key と同じ本文**だけを再送し、サーバーはそのキーの記録で答える（二重出力しない） |
| 新規 | 完了後の「別の設定で再出力」は新しいキー・新しいフォルダ（値が同じでも別要求） |
| JSON 未選択 | v1 の出力形式（新規 Preset、画面の共通差分）で JSON を外すと「このジョブは後から再出力できません」を表示 |
| 操作性 | Vuetify 標準部品。キーボードで開ける、取消に初期フォーカス、閉じると開いたボタンへ戻る。日英 |

対象外（#82 の確定範囲）: 認識本文・時刻・単語・話者割当の編集、ASR／話者分離／モデル初期化・取得／worker による再認識／ASR キュー投入、内部 cache の利用、再出力結果からの再々出力、一括再出力、内蔵 Viewer。一括は API が単件・Idempotency-Key 単位のため、将来クライアントが複数要求を並べる形で追加でき、現設計はそれを妨げない。

## 2. 情報充足性と共通レンダラ（先行調査の結論）

5 形式の exporter（`src/utteran/exporters/*.py`）が読む値は次だけである。

| 形式 | 使う値 |
|---|---|
| SRT / VTT / TXT | セグメントの start・end・text・speaker |
| MD | 上記＋`input_path`（stem と表示）、`created_at`、duration、ASR の backend/model/device、話者分離の有無と backend/model/device |
| JSON | 上記＋speaker_confidence、単語（start・end・text・probability）、diarization.memory |

`TranscriptionResult.language`、`transcription.segments`、diarization の turns／exclusive_turns／num_speakers はどの形式も使わない。JSON v1 はこれらを持たないが、再出力にも不要なので補完・捏造しない。

- **読み取りモデル** `TranscriptView`（`utteran_contracts.transcript_export`）は上表の値だけを持つ。core の exporter は `PipelineResult` からこのモデルを作り（`utteran/exporters/_view.py`）、GUI は JSON v1 から作る。**書式化コードは 1 か所だけ**で、core と GUI が同じ関数を使う（GUI は core を import しない。`utteran_contracts` は core を import しない）。
- 数値は読み込んだ型のまま（int は int）。`PipelineResult.from_dict` のような `float()` 変換をしないので、`"duration": 1` は `1` のまま再出力される。
- **厳格な読取り** `parse_json_v1`: UTF-8（BOM なし）、キー集合と型が完全一致、`bool` を数値として扱わない、NaN／Infinity と重複キーを拒否、`memory` は object か null（深さ 32 まで）、セグメント 20 万・単語 200 万・話者ラベル 256 まで。
- **無損失の確認**: 読み取ったモデルを変更なしで JSON に戻し、LF または CRLF で**元のバイト列と完全一致**することを必須とする。手で編集された・他のツールが書いた・`speakers` が本文と矛盾する JSON は `source_noncanonical` で拒否する。これにより、読取りモデルがファイルの全情報を保持したことを毎回証明する。
- **golden**: `tests/fixtures/b2/exports.json`（pre-B2 の固定バイト列）の JSON から、同じ BOM/CRLF で SRT/VTT/TXT/MD/JSON がすべてバイト一致する。さらに合成結果（時刻の丸め・1 時間超・空テキスト・話者なし・空ラベル・改行入りテキスト・単語あり／なし・話者分離あり／なし）× 5 種のオプションで、core exporter → JSON → 共通レンダラの出力が core exporter とバイト一致する（`tests/test_ec_reexport.py`）。既存の exporter 試験・b2 golden・CLI 試験は無変更で PASS。

### 2.1 話者ラベルの意味（形式固有の制約）

- JSON の `speaker` は**元ジョブの話者ラベル変更を適用した後**の値で、元の話者 ID は保持されない。再出力のラベル変更は、この表示上のラベルに 1 回だけ適用する（元の mapping は再適用しない）。元の変更が空なら、結果は core が元 ID に変更を適用した場合と同じ（試験で合成写像と比較）。
- 同じ名前に変更された複数の話者は区別できず、まとめて変更される。元 ID への逆変換は保証しない。ダイアログはこの旨を表示する（`original_labels_applied`）。
- `show_speaker` は SRT/VTT/TXT/MD の行頭表示だけに効く。**JSON は常に全セグメントの話者を含む**（既存 JSON exporter の仕様。変更していない）。ダイアログに明記。
- 空文字のラベルは既存どおり「ラベルなし」と同じ表示（行頭に付けない）で、JSON には `""` のまま残る。
- SRT BOM は SRT にだけ適用される。SRT を選ばずに BOM を指定した要求は `option_not_applicable`（UI では無効化）。

## 3. 入力元の検証（`reexport_files.read_verified_json`）

画面の verified 表示ではなく、サーバーが毎回次を確認する。

1. ジョブが `succeeded`、`If-Match` の revision と一致（実行時）、per-job ディレクトリ（0.1.54 以降の `output_stamp`）であること。legacy は所有範囲を証明できないため `source_legacy`。
2. 完了時の形式別証跡（`artifact_evidence`）があり、`json` の記録があること（なし／旧形式は `source_unverified`、JSON 未出力は `source_json_not_recorded`）。
3. 出力親ディレクトリを開き、候補名（id 長 8〜32）の子を `dir_fd` 相対・`O_NOFOLLOW` で開く。marker も同様に開き、このジョブ ID を名指しすること。リンク・別ジョブの marker は不一致として扱う。
4. `<name>.json` を `dir_fd` 相対・`O_NOFOLLOW|O_NONBLOCK` で開き、`fstat` で通常ファイル、リンク数 1（ハードリンク拒否）、サイズが記録と一致かつ上限以下を確認。FIFO などは開いた直後に `source_special`。
5. その記述子から**1 回だけ**全バイトを読み（記録サイズ＋1 バイトまで）、読取り前後の `fstat` が一致し、SHA-256 が記録と一致することを確認。**このバイト列そのもの**を解析する（パスを開き直さない）。読取り中の変更・差替えはハッシュ不一致として拒否される。
6. ダイアログが表示した `source_sha256` と実行時の照合結果が一致しなければ `source_changed`（計画と実行の間の差替え・すり替え防止）。
7. 照合に使ったジョブのフォルダの `(st_dev, st_ino)` と、その時点のパスを返す。出力先はこのフォルダとその子孫を拒否する（§4 の 1a）。

## 4. 出力の安全性（`reexport_files.create_output`）

競合モデル: 同一ユーザーの別プロセスが、任意の時点で名前の変更・差替え・リンク作成を行い得る（C2c §5.0）。したがって「名前で確認してから名前で操作」はしない。

1. 保存先は絶対パス・ローカル（`//` は拒否）・正規化し、各要素を `lstat` してリンクがあれば `output_parent_link`（B7 の出力先規則と同じ。分かりやすい理由表示のため）。そのうえで `/` から**各要素を `openat(O_DIRECTORY|O_NOFOLLOW)` で順に開いて**親を記述子に固定する。確認の後に途中の要素がリンクへ差し替えられても、この walk が止まり `output_parent_changed` になる（独立レビュー P2-3）。
1a. **元ジョブのフォルダの保護**（オーナーレビュー P2-2）: 正規化した保存先が、§3 で照合したジョブのフォルダのパスと同じかその下なら拒否する（名前による判定。同名の別フォルダに差し替えられた場合も含む）。さらに、固定した walk の**全要素**（`/`、各祖先、保存先）の `(st_dev, st_ino)` のどれかがジョブのフォルダと同じなら拒否する（identity による判定。フォルダが移動・改名されていても検出する）。どちらも `output_inside_source`（409）。固定した後に保存先がジョブのフォルダの中へ**移動された**場合に備え、フォルダ作成の直前と、作ったフォルダを開いた直後にも、保持した記述子から `..` を `/` までたどって全祖先の identity を照合する（名前を使わない。たどれなければ拒否）。作成前なら `output_inside_source`（何も作らない）、作成後なら書込みをせず `failed`（`partial: true`、空のフォルダが残る）（独立再レビュー P2-a）。
2. **Git checkout の確認**（core の `_ensure_git_ignored_output` と同じく `git check-ignore --no-index`）。判定対象は**実際に作る全ファイル名**、つまり各候補フォルダ名について `<name>/.utteran-reexport`（marker）と `<name>/<name>.<形式>` の全形式（オーナーレビュー P2-1）。`--quiet` は複数パスのうち 1 つでも除外されていれば成功を返すため、**1 パスずつ**問い合わせ、1 つでも除外されていなければ `output_in_repository`。終了コード 0／1 以外は `output_git_unverified`。Git には GUI の環境から `GIT_DIR`・`GIT_WORK_TREE`・`GIT_INDEX_FILE`・`GIT_CEILING_DIRECTORIES`・`GIT_CONFIG*` などを除いて渡す（別のリポジトリの除外設定で「除外済み」と誤判定しないため）。狭い除外パターン（特定のファイル名だけ）や否定パターン（`!…`）で一部の実ファイルが見える設定、marker が除外されない設定はいずれも拒否される。`.git` は名前ではなく**固定した祖先の記述子**から探し、Git は見つけた祖先の記述子（`/proc/self/fd/<n>`）を作業ディレクトリとして、そこからの相対パスを判定する。`/proc` がなく記述子を渡せない場合は `output_git_unverified`（fail closed）。既存 CLI 側の同種の確認（core の `private-transcript.<ext>` probe）は変更していない（範囲外）。
2a. **記録の前に拒否する**: 1〜2 の判定（保存先の固定、元ジョブのフォルダ、パス長、Git）は、再出力 ID を先に決めて全候補フォルダ名について、`pending` 記録の作成**前**に行う。拒否時は記録もフォルダも作られない。フォルダ作成直前にも同じ Git 確認をもう一度行う（その間に除外規則が変わった場合は `failed`・`partial: false`）。
3. 固定した親に対し `mkdir(name, 0700, dir_fd=…)`。既存の名前（ディレクトリ・ファイル・リンク・大小文字違いで衝突するもの）は `FileExistsError` になり、再利用せず次の長い ID 形式へ進む。全候補が埋まっていれば `output_name_unavailable`。
4. 作ったフォルダを `dir_fd` 相対・`O_NOFOLLOW` で開き、ディレクトリかつ所有者が自分かつ**空**であることを確認。違えば（作成と open の間に置き換えられた）`output_claim_lost` で何も書かない。POSIX の `mkdir` は作成した inode を返さないため、同じユーザーが**空の**ディレクトリに差し替えた場合は検出できない（その場合も書込みは開いたディレクトリだけに行われ、上書き・リンク追従は起きない）。
4a. **書込み直前の再検証**（オーナー再レビュー P2-4）: フォルダ名の記録（DB のコミットを含み、時間がかかり得る）の後、marker の作成前と各形式のファイルの作成前に毎回、(1) 表示する絶対パスを `/` から名前でたどり、固定した全祖先・保存先・新しいフォルダの identity と一致すること（§4 の 6 と同じ `reaches`）、(2) 保持した新しいフォルダの記述子から `..` でたどった祖先にジョブのフォルダがないこと、を確認する。一致しなければ以後のファイルを書かず `failed`（`output_moved` または `output_inside_source`、`partial: true`）。パスの一致は、固定時に Git 判定をした同じディレクトリの並びであることも意味するので、記録中に Git checkout の下へ**移動**された場合もここで止まる（移動ではなく、変わらない祖先に `.git` が新しく作られた・持ち込まれた場合や `.gitignore` が変わった場合は対象外。§11）。ジョブのフォルダの中への移動も通常はパスの不一致として `output_moved` になる（`output_inside_source` は 2 つの確認の間の競合でだけ出る）。止めた場合、記録のフォルダパスは「作成時の場所」として表示し、現在の場所は断定しない。
5. フォルダ名を記録（§6）してから、marker と各ファイルを `O_CREAT|O_EXCL|O_NOFOLLOW`（0600）で `dir_fd` 相対に作成し、全バイトを書き、`fsync`。最後にフォルダと親を `fsync`。既存の名前（差し込まれたリンクを含む）には書かない（`output_claim_lost`）。
6. **位置の確認**（オーナーレビュー P2-3）: 全ファイルの fsync 後、表示する絶対パス（保存先＋新しいフォルダ名）を `/` から**名前で改めて**、各要素を `O_DIRECTORY|O_NOFOLLOW` で開いてたどる（読取りだけで、何も作らない）。たどった `/`・各祖先・保存先・新しいフォルダの `(st_dev, st_ino)` が、固定した記述子のもの（walk の時点で記録）と**すべて**一致したときだけ `location_verified: true`。親や上位の祖先が改名・移動・差替えされた場合、リンクに変わった場合、たどれない・確認できない場合はすべて `false`（成功と誤認させない。UI に「移動・置換の可能性」を表示）。これは**確認した時点の観測**であり、その後の移動・改名に対する保証ではない。

**削除しない**: 失敗・中断のどの場合も、作ったフォルダや途中のファイルを削除・rename・上書きしない（名前による削除は検証済み inode を消す保証がないため。成果物削除を全 OS で無効とした C2c の判断と同じ）。記録に `partial: true` を付け、画面に「途中までのファイルが残る可能性」を表示する。元ジョブのファイル・フォルダ・marker・入力音声には書込み系の操作を一切行わない（読取りだけ）。

**出力サイズ**: 全形式をメモリ上で生成し、合計が上限を超えればファイルを作る前に `output_too_large`。

### 4.1 対応 OS と BLOCKED

| OS | 状態 | 理由 |
|---|---|---|
| Linux | 対応（Cloud Linux の pytest・E2E で検証） | `open`/`mkdir`/`stat` の `dir_fd`、`O_NOFOLLOW`、`O_DIRECTORY`、`fdopendir` が揃う |
| macOS | 同じ判定で有効になるが**未検証**（CI なし） | 機能判定（`supported()`）は同じ条件 |
| Windows | **0.1.70 から対応（ローカル NTFS のみ、VM 受入待ち）**。内部 wheel `utteran_native_fs` が無ければ従来どおり `reexport_platform_unsupported` | Rust core（[E-C2a ADR](e-c2a-rust-safe-fs.md)）の `utteran_gui/reexport_files_native.py`。全祖先を `FILE_SHARE_DELETE` なしで保持（rename／削除不可）、新規フォルダは作成と同時に open、marker を最初に書き、各ファイル前に保持 handle と到達性を再確認、排他作成。Git は祖先に `.git` があれば常に拒否（`.gitignore` を見ない）。UNC・SMB・SUBST／per-logon の drive・FAT/exFAT/ReFS・reparse point は拒否。フォルダの flush は不可、ACL は親から継承 |

## 5. 入力・出力の上限と実測

`tools/ui3/measure_reexport.py`（合成値のみ）で実測。Cloud Linux、Python 3.11。1 件の JSON v1 を共通レンダラで作り、SHA-256＋厳格読取り（無損失確認を含む）と全 5 形式の生成を行う。

| ソース | セグメント | 読取り | 5 形式生成 | 出力合計 | 実行前 RSS → ピーク |
|---:|---:|---:|---:|---:|---:|
| 1.0 MB | 498 | 0.07 s | 0.05 s | 1.5 MB | 38 → 44 MiB |
| 8.3 MB | 3,986 | 0.58 s | 0.42 s | 11.7 MB | 102 → 150 MiB |
| 16.7 MB | 7,973 | 1.27 s | 0.87 s | 23.5 MB | 174 → 280 MiB |
| 33.4 MB | 15,947 | 2.61 s | 1.71 s | 47.0 MB | 320 → 533 MiB |
| 66.8 MB | 31,895 | 5.38 s | 3.32 s | 94.0 MB | 610 → 996 MiB |

（RSS の「実行前」は合成データの生成分を含む。増分が読取り・生成の負担。各セグメント 12 単語・3 文の日本語。）

| 設定値 | 値 | 根拠 |
|---|---:|---|
| ソース JSON | 32 MiB（`MAX_SOURCE_BYTES`） | 一覧の `HASH_LIMIT` 64 MiB では約 9 秒・約 390 MiB 増。32 MiB で約 4.3 秒・約 210 MiB 増。合成で約 1.6 万セグメント（12 単語ずつ、約 15 時間の発話相当）を含む |
| 出力合計 | 128 MiB（`MAX_OUTPUT_BYTES`） | 32 MiB ソースの 5 形式で約 47 MB。最長ラベル（256 文字）適用時の増加を見込んだ余裕 |
| セグメント／単語／話者ラベル | 20 万／200 万／256 | 32 MiB 内に収まる構造の上限。超過は `source_too_large` |
| 話者ラベル | 256 文字、制御文字不可 | B5 `OutputSettings.speaker_labels` と同じ長さ。画面で欄を空にした場合は「変更しない」として扱う（API では空文字を既存の意味＝行頭ラベルなしとして受け付ける） |
| 同時実行 | プロセスあたり 1 件 | 上記の CPU・メモリを重ねない。2 件目は `reexport_busy`（503、再試行可）。data root ごとの OS ロックにより GUI プロセスは 1 つ |

再出力は ASR の heavy slot を使わない（モデル・GPU を使わない）。ジョブの実行中でも可能。

## 6. API・状態・永続化

既存の v1 サブアプリに登録する（session・Host・Origin の境界は B5/B7/E-B と同じ）。既定 GUI では import もしない。ルートはジョブの `POST /jobs/{id}/{action}` より前に登録する。

| メソッドとパス | 用途 | 成功 | 主なエラー |
|---|---|---|---|
| `GET /jobs/{id}/reexport-source` | 可否（`available`／`refusal` の固定コード）、凍結済みの初期値、表示上の話者ラベル、ソースの SHA-256・サイズ・改行・件数、既定の保存先、上限。**読取りのみ**（ストアも作らない） | 200 | 404、410 `job_deleted` |
| `POST /jobs/{id}/reexports` + `If-Match: "<job revision>"` + `Idempotency-Key` | 実行 | 201 新規記録、200 このキーの既存記録 | 428／400、412 `revision_conflict`、409（拒否コード・`idempotency_conflict`・`reexport_limit`）、413、422、503 `reexport_busy` |
| `GET /jobs/{id}/reexports` | このジョブの記録（新しい順） | 200 | |
| `GET /reexports/{rex_id}` | 1 件（結果不明後の照合） | 200 | 404 `reexport_not_found` |

本文（`schema_version: 1`、未知キー不可）: `source_sha256`、`formats`（重複なし・1 件以上。サーバーが B7 の順に正規化）、`show_speaker`、`srt_bom`、`newline`、`speaker_labels`（キーは JSON の表示ラベル。未知は `speaker_label_unknown`）、`output_parent`（`null`＝元の出力先）。

エラーは `{schema_version, error:{code, message_key:"reexport.<code>", request_id, retryable, issues:[]}}`。値・パス・ラベルを含まない。`retryable` は `reexport_busy`・`store_busy`・`store_unavailable` だけ true。

### 6.1 実行順序と冪等性

1. 本文の厳格検証。既知の Idempotency-Key ならその記録で答える（同じ本文なら 200、違えば 409 `idempotency_conflict`。新しい作業はしない）。
2. プロセス内ロック（1 件）。
3. ジョブ・snapshot・証跡を**読取りだけ**（B7 ストアに書かない）。revision 不一致は 412。
4. §3 の入力元検証と、表示した `source_sha256` との一致。
5. 全出力をメモリで生成し上限を確認（ここまでの拒否は記録を作らない）。
6. `pending` 記録をコミット（キー、本文全体＋ジョブ ID＋revision の HMAC digest、保存先）。同じトランザクションでキーの重複と件数・容量上限を確認。
7. §4 のフォルダ作成→フォルダ名を記録→ファイル作成・fsync。
8. `completed`（形式ごとの SHA-256・サイズ、`location_verified`）または `failed`（固定コード、`partial`）。

- **結果不明・再送**: クライアントは同じキーと本文だけを再送する。記録があればその状態を返し、実行しない。要求がサーバーに届いていなければ、そのとき初めて実行される。
- **ABA**: 同じ値でも新しい操作は新しいキー＝新しい記録・新しいフォルダ。値が同じことで未知の旧要求が失効したとはみなさない（キーでだけ同一性を判断）。
- **途中 kill・再起動**: 記録は所有プロセスのトークンを持つ。別プロセス（再起動後）や、確定の書込みに失敗した `pending` は `interrupted`（結果不確定、`partial`＝フォルダ名が記録済みならファイルが残る可能性）と表示し、**同じ要求を再実行しない**。新しい再出力は新しいキーで行う。
- **DB 確定の前後**: 6 より前の失敗は何も作らない。6 の後 7 より前の失敗は `failed`（`partial: false`）。7 の途中は `failed`（`partial: true`）または `interrupted`。8 のコミット失敗は `interrupted`（ファイルは完全な場合もある＝不確定として扱う）。
- **並列**: 同じキーの同時要求は 1 件だけが実行し、他は `reexport_busy` か既存記録。実行中のキーは `pending` 記録のコミット前にプロセス内で登録するので、その間に届いた同じキーの再照合は `pending`（実行中）と答え、`interrupted` と誤らない（独立レビュー P2-1）。別キーの同時要求は 1 件ずつ。可否表示（`reexport-source`）はソース全体を読み直すため、同時 2 件まで（超過は `reexport_busy`）。
- **最後の記録**: ファイルを fsync した後の `completed` の書込みが `store_busy` なら最大 4 回まで再試行する。それでも失敗したら `interrupted`（結果不確定）と表示する。

### 6.2 ストア（`reexport-v1`）

- **パス**: `DataPaths.reexport_store`。通常は `user_data_dir("utteran")/reexport-v1/reexports.sqlite3`、明示 data root では `<UTTERAN_DATA_ROOT>/data/reexport-v1/reexports.sqlite3`。B5・B6/B7・E-B のファイルとは別で、それらを読み書き・prune しない。
- **作成**: 最初の実行（`pending` の書込み直前）だけ。ディレクトリ 0700、ファイル 0600、symlink 拒否、O_EXCL 作成者プロトコル、`mode=rw` で開き消えたファイルを再作成しない。
- **作成途中の停止**（O_EXCL 作成後・スキーマ確定前の kill）: 0 バイトのファイルが残る。読取りは `store_initializing`（503、再試行可）、書込みは作成者の待機時間を過ぎると `store_incomplete`（409）を返し、どちらも自動で初期化しない（独立レビュー P2-2）。復旧はアプリを終了し、0 バイトであることを確認してから `reexport-v1/reexports.sqlite3` を退避すると、次の再出力で新しく作られる。
- **スキーマ（`user_version=1`）**: `installation(id, secret)`、`reexports(id PK, job_id, job_revision, key UNIQUE, digest, state∈{pending,completed,failed}, error_code, partial, created_ms, updated_ms, owner, request, parent, directory, source_sha256, source_size, files, location_verified)`。
- **記録する値**: 実行時刻、形式、表示設定とラベル変更（要求の正規 JSON）、保存先と新しいフォルダ名、状態、形式別の SHA-256・サイズ、ソースの SHA-256・サイズ。**認識本文・JSON の複製は保存しない**。
- **トランザクション**: `BEGIN IMMEDIATE`、`synchronous=FULL`、DELETE journal、`secure_delete=ON`、`auto_vacuum=INCREMENTAL`。未知の `user_version`・`quick_check` 失敗は拒否し、ファイルを変更しない。
- **上限（明示拒否、自動削除なし）**: 全体 2,000 件・ジョブあたり 100 件（`reexport_limit`）、1 要求 1 MiB（`request_too_large`。表示ラベル 256 個と新ラベル 256 個が各 256 文字・4 バイトでも 512 KiB に収まる。HTTP 本文の上限は 1 MiB＋64 KiB）、要求の合計 64 MiB（`store_quota_exceeded`）。上限に達しても記録・成果物を消さない。記録の削除 UI は本束にない（残留、§11）。
- **物理容量の実測**（8 ラベル×256 CJK 文字・長い保存先パスの最悪寄りの要求 6,717 B）: 100 件 868 KB、1,000 件 8.4 MB、2,000 件 16.7 MB（空きページ 1）。要求合計の上限 64 MiB と管理領域で、ファイルは最大でも約 70〜80 MB に収まる。pytest で 60 件（256 文字ラベル）が 256 KiB 以下・空きページ 0 を毎回確認する。
- **元ジョブ履歴の削除（C2c ③）との関係**: 履歴を墓標化しても再出力記録は残る（ジョブは 410 になり、ダイアログは開けない）。記録とフォルダは削除しない。記録内の保存先パスとラベルは残留する（§11）。

## 7. ログと機密

- `reexport_*.py` は `logging` を import しない（試験で確認）。新しいログファイルは追加していない。GUI ログは既存の `logging_runtime` のまま。
- 話者ラベル・保存先パスは、認証済みの要求・応答本文とローカル画面だけで扱う。エラー本文・URL・SSE・公開診断・ブラウザの storage には出さない（pytest でラベル・パス・sentinel がエラー応答とログに出ないこと、Vitest で URL に出ないこと、E2E で storage・URL に出ないことを確認）。
- private DB は B5・E-B と同様に平文。保持・削除方針は owner の未決事項を引き継ぐ。

## 8. 試験

| 層 | ファイル | 主な確認 |
|---|---|---|
| Backend（pytest） | `tests/test_ec_reexport.py` | 共通レンダラと core exporter のバイト一致（5 形式×5 オプション×話者分離有無、空・単語なし・話者なし）、b2 golden の再現、表示ラベルへの再変更＝合成写像、同名ラベルを分離しない、不正 JSON 12 種・バイト改変 10 種・構造上限、命名規則、正常系で元フォルダ・入力・B5/B7 DB のバイト不変とジョブ数不変、同じキーの replay・別本文 409・同値の新キーは別フォルダ、revision・digest・ラベル・オプション・保存先の拒否 13 種（記録なし・ファイル不変）、明示保存先と元ジョブフォルダ内、ソースの改変・削除・symlink・ハードリンク・FIFO・別 marker、ジョブフォルダの symlink 差替え、JSON 未出力・失敗ジョブ・legacy・証跡なし・非対応 OS、既存名の非再利用とリンク非追従、mkdir と open の間のフォルダ差替えで書かない、ファイル名へのリンク差込みで被害なし、親のリンク要素と Git checkout、ENOSPC／EROFS／EIO で `failed`＋途中ファイル保持、mkdir 失敗、crash 後の `interrupted` と再実行しないこと、同時実行（busy と同じキー 4 並列で 1 フォルダ）、件数・容量上限で拒否し何も消さない、出力・ソースの上限、物理容量、未知 schema を変更しない、HTTP（428/400/201/200/404、session・Origin、エラーに値なし、ログに値なし）、core/重い import なし、既定 GUI にルート・import なし |
| Unit（Vitest） | `frontend/src/domain/v1/reexport.test.ts` | 応答の形の検証、凍結初期値とラベルを空から始める、1 回だけ送る（If-Match・キー・本文）、BOM の無効化と入力検証、拒否の表示、結果不明で固定・閉じない・同じキーと本文の再確認、完了後は新キー（ABA）、4xx 後の編集で新キー、閉じた後の遅い応答を適用しない、URL に値を出さない |
| Backend（オーナーレビュー回帰） | `tests/test_ec_reexport.py` の `test_owner_p2_*` | P2-1: 仮名だけの除外、marker だけが除外されない、否定パターンで Markdown が見える、2 形式のうち 1 つだけ除外、のいずれも拒否し記録・フォルダなし／ディレクトリ単位・全ファイル・`*_reexport_*/` の除外は受理し `git status` に出ない。P2-2: 元ジョブのフォルダ・その子・孫を拒否し記録なし・ファイル不変／読取り後に元のフォルダを保存先パスへ移動しても identity で拒否。P2-3: 親・祖父の改名、改名後の同名再作成（4 通り）で `location_verified: false`、移動なしでは `true` |
| E2E（Playwright、実 FastAPI） | `frontend/e2e/reexport-ec.v1.spec.ts` | 新規 Preset の JSON 既定と未選択警告、キーボードで開く・取消に初期フォーカス・閉じるとボタンへ戻る、凍結初期値、形式の複数選択・BOM 無効化・CRLF・ラベル変更、新しいフォルダの内容（バイト確認）、元フォルダ不変、ジョブ投入・再実行なし、別の設定での再出力は新キー、JSON 未出力と改変 JSON の拒否理由、英語表示、応答喪失で閉じない・再確認は同じキーと本文で 1 フォルダ、アプリ再起動後の記録表示 |

合成の E2E データ: `tools/ui2/synthetic_v1.py` の SyntheticRunner は、入力名が `reexport-` で始まるときだけ共通レンダラで JSON v1 も書く（他の既存 E2E は SRT のみのまま）。

試験件数、最終 HEAD、push／PR CI の URL と結果は Draft PR の本文に記録する。

## 9. 独立レビュー

Draft PR 提出前に、実装と別コンテキストのサブエージェント（general-purpose、実装の会話文脈を持たない）が初版 `33e91b2` の差分全体・制御経路・不変条件・試験を読み、`tests/test_ec_reexport.py` を実行してレビューした。結果は **P0 0 件、P1 0 件、P2 3 件、P3 11 件**。

| ID | 指摘 | 対応 | 回帰試験 |
|---|---|---|---|
| P2-1 | `pending` のコミット直後、実行中の登録前に届いた同じキーの再照合が `interrupted` と誤答し、画面が確定扱いにする | 実行中のキーをコミット前に登録し、それで判定 | `test_review_p2_1_…`（旧コードで失敗を確認） |
| P2-2 | ストア作成途中の停止（0 バイト）で読取りが 500、書込みが永続的に拒否・理由不明 | `store_initializing`（503）／`store_incomplete`（409）の固定コード、自動初期化はしない、復旧手順を §6.2 に記載 | `test_review_p2_2_…` 2 件 |
| P2-3 | 保存先の途中の要素を確認後にリンクへ差し替えると、別の場所（Git checkout を含む）へ書けた | `/` から `O_NOFOLLOW` で各要素を開いて固定、Git の判定も固定した記述子で行う | `test_review_p2_3_…` 2 件 |
| P3-1 | `formats` にハッシュ不能な要素で 500 | 型を先に検査し `formats_invalid` | `test_review_p3_unhashable_…` |
| P3-2 | フォルダ名の記録に失敗すると空フォルダが残るのに `partial: false` | `interrupted` は常に `partial: true` | `test_review_p3_2_…` |
| P3-3 | 最後の DB 書込みの一時的な busy で完了が `interrupted` に固定 | busy を最大 4 回再試行 | 同上（`store_busy`×2 → `completed`） |
| P3-4 | 本文上限が最大の正当な要求より小さい | 1 MiB（＋64 KiB）へ | 計算根拠を §6.2 に記載 |
| P3-5 | ソースの親ディレクトリの途中要素を固定していない | 出力と同じ walk で固定 | `test_review_p3_5_…` |
| P3-6 | `mkdir` 直後の空ディレクトリへの差替えは検出不能 | POSIX の制約として §4 に明記 | — |
| P3-7 | 可否表示がロック外で毎回全体を処理 | 同時 2 件に制限（`reexport_busy`） | — |
| P3-8 | ラベル欄を空にすると話者名が消える | 画面では空欄＝変更なし、説明を表示 | Vitest |
| P3-9 | 送信中・結果表示でフォーカスがモーダル外へ外れる | 状態ごとに進行表示・再確認・拒否・閉じるへ移動 | E2E（キーボード操作） |
| P3-10 | 記録上限が恒久的 | 削除 UI なしで上限到達時は拒否、owner 判断として §11 に残留 | — |
| P3-11 | 再起動 E2E のサーバー起動待ちに期限がない | 30 秒で失敗 | — |

レビューで確認済みとされた点: 元ジョブのファイル・marker・入力への書込みがないこと、ソースを記述子で 1 回読み同じバイト列を解析すること、出力がすべて `O_EXCL|O_NOFOLLOW`・fsync・削除/rename なしであること、厳格読取りと無損失確認、core exporter のバイト同等性、HMAC digest の一貫性と同じキー別本文の 409、再起動後に再実行しないこと、画面の結果不明の扱い（同じキーと本文の再確認、完了後は新キー）、エラー本文に値がないこと、境界（既定 GUI・core・worker・キュー非接触）。レビューで未検証とされた点（macOS、Windows 実機、停電時の耐久性）は §11 のとおり。

### 9.1 オーナーレビュー（PR #83、HEAD `758a244`）

[オーナーレビュー](https://github.com/zawa356/Utteran/pull/83#pullrequestreview-6051687191) の P2×3 を修正した。各回帰試験は修正前のコードで失敗することを確認してから修正した（Red コミット `791826a`）。

| ID | 指摘 | 対応 |
|---|---|---|
| P2-1 | Git 除外判定が仮のファイル名（`private-transcript.<ext>`）を対象にしており、実ファイルと marker が Git に見える場合がある | 実際に作る全ファイル名と marker を 1 件ずつ判定、固定記述子の方式は維持、記録の前に判定（§4 の 2・2a） |
| P2-2 | 元ジョブのフォルダとその中を保存先にできた（試験も正常系として許容していた） | 名前と identity の両方で拒否、記録の前に判定、旧試験を拒否・不変の回帰へ置換（§4 の 1a） |
| P2-3 | 親・祖先が移動しても `location_verified` が true | 表示パスを `/` から改めてたどり、全要素の identity を照合、不能時は false、時点の観測と明記（§4 の 6） |

修正後、別コンテキストのサブエージェントが `758a244..0390614` を再レビューした（P0 0、P1 0、P2 1、P3 3）。

| ID | 指摘 | 対応 |
|---|---|---|
| P2-a | 元ジョブのフォルダの判定が固定時の 1 回だけで、その後に保存先をジョブのフォルダへ移動すると中に書けた（簡易実験で再現） | フォルダ作成直前と作成直後に `..` で全祖先を照合（§4 の 1a）。回帰 2 件（照合を無効にすると失敗することを確認） |
| P3 | 継承した `GIT_DIR` 等で別リポジトリの除外設定を読む | Git の環境から除外（回帰 1 件） |
| P3 | 別プロセスが同じキーの記録を作った直後に、こちらが記録前の拒否を返す | 記録前の拒否の際に同じキーの記録があれば replay で答える |
| P3 | ジョブのフォルダの**下位**フォルダを別の場所に bind mount した場合は検出できない（権限が必要） | 残留として §11 に記載 |

### 9.2 オーナー再レビュー（HEAD `ecc7901`、P2-4）

[再レビュー](https://github.com/zawa356/Utteran/pull/83#pullrequestreview-6052675561): `on_claimed()`（DB 処理）の間に保存先を移動すると、移動先（元ジョブのフォルダ配下や Git checkout 配下）へ marker・転写ファイルを書けた。§4 の 4a の書込み直前の再検証を追加。回帰（Red `5751cc6`、修正前に 7 件失敗）: 記録中に親・祖父を移動（4 通り）、ジョブのフォルダへ移動、除外なしの Git checkout へ移動（`git status` に何も出ない）、ファイルの間の移動で次のファイルを書かない。最後のファイルの後の移動は `location_verified: false`。

同じサブエージェントが修正差分（`0390614..b3fa403`）を確認した結果は P0／P1／P2 なし、退行なし（移動の再現スクリプトは修正後に作成前で拒否）。P3 1 件（記録前の拒否時の replay が、自分が作った `pending` 記録を返し得る）は、別プロセスの記録だけを replay するよう修正した。

## 10. 互換性と変更しないもの

- core の 5 exporter は共通レンダラへ委譲したが、出力バイトは不変（既存 exporter 試験、b2 golden、application golden で確認）。`Exporter.write` は同じバイト列を `write_bytes` で書く。
- 既定 GUI（`create_app(session_key, repo_root=...)`）は再出力のモジュールもルートも読み込まない（試験済み）。旧 `--next-ui` と CLI は無関係。
- B5 の API・DB、B6/B7 の制御 DB（スキーマ・行）、ジョブ snapshot、キュー、E-B の作業セットは変更しない。B7 ストアは読取りだけ。
- 既存 Preset/Policy の revision、初期化済みストア、保存済み作業セット、既存 snapshot、旧 GUI/CLI の既定値は変更していない。新規 Preset の既定形式は以前から `srt, json, md`（JSON を含む）なので既定値の変更は不要だった。追加したのは JSON を外したときの警告表示だけ。
- D3-B1 の外部アプリ起動 BLOCKED、成果物削除の全 OS 無効、Git checkout への private 出力の防止を維持する。

## 11. 制約・残留課題

- **Windows は 0.1.70 で有効（§4.1）だが Windows 11 実機の受入は未完了**（[VM 引継ぎ](e-c2a-windows-vm-handoff.md)）。保持中の新規フォルダの reparse 化（W-14）で問題が見つかれば無効に戻す。macOS は未検証。
- Windows 11 実 WebView2、ACL、reparse、OneDrive、長パスの実機受入は [#22](https://github.com/zawa356/Utteran/issues/22) の別ゲート。CI・合成試験の成功を実機確認と混同しない。
- 作成した直後のフォルダ、保存先、その祖先を同じユーザーの別プロセスが移動・改名・差替えした場合、ファイルは書込みに使ったディレクトリ（移動先）に存在する（書込みは記述子に固定）。完了時の位置確認で検出し `location_verified: false` と表示する。位置確認は時点の観測で、確認の後の移動は検出しない。
- **検査と書込みは不可分ではない**（オーナー再レビュー P2-4）: POSIX では同じユーザーのプロセスによる任意の rename を防げない。書込みの直前（各ファイルの作成前）に到達性と祖先を確認するが、その確認の直後から、そのファイルの作成・書込み中・最後のファイルの後までの間に保存先や祖先が移動された場合は防げない。最後のファイルの後の移動は完了後の位置確認で `location_verified: false` として報告する（防止ではなく報告）。ファイル作成と確認の間の短い区間で移動された場合は、そのファイル 1 つが移動先に書かれ得る（排他作成のため上書きはしない）。空ディレクトリへの差替え、bind mount も同様に検出できない場合がある。絶対的な保証が必要な場合は、対象の制限か fail closed の設計（オーナー判断）が必要。
- Git の判定は、フォルダ作成直前に行った確認の時点の `.git` と `.gitignore` による。その後（記録中・書込み中を含む）に、移動されていない祖先へ `.git` が作られた・別の場所から持ち込まれた場合、または除外規則が変更された場合は検出しない（パスの到達性は変わらないため）。Git の応答待ちの期限（10 秒）を超えた場合は `output_git_unverified`（409、再試行不可として扱う）。
- 元ジョブのフォルダの判定は、名前・固定時の identity・作成直前と直後の `..` による祖先照合で行う。照合と `mkdir` の間の瞬間に移動された場合は、空のフォルダがジョブのフォルダ内に残り得る（ファイルは書かない）。ジョブのフォルダの下位フォルダを bind mount した別の場所は検出できない（mount 権限が必要な操作）。
- 失敗・中断時の途中ファイルは削除しない（手動で削除する）。
- `mkdir` 直後に同じユーザーが空のディレクトリへ差し替えた場合は検出できない（§4）。停電時の耐久性は fsync の順序に依存し、実機で未検証。
- 記録の上限（全体 2,000 件・ジョブあたり 100 件）は削除 UI がないため恒久的。上限到達時の扱い（記録の削除・保持期間）は owner 判断。
- 再出力記録の削除・保持期間・エクスポートは未実装（B5／E-B と共通の owner 未決事項）。上限到達時は新しい再出力を拒否する。元ジョブ履歴を削除しても記録（保存先パス・ラベル変更）は残る。
- 元の話者 ID の復元、同名話者の分離はできない（§2.1）。
- E-B の P3（active fence 再試行バックオフ）、CI 最適化、束 F、既定 GUI の切替、正式 Release は範囲外。
