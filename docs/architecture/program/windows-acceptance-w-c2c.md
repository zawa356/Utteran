# W-C2c — Windows実機受入指示書（束C2c: 履歴の墓標化・成果物削除）

更新: 2026-11-18 JST（開発版`0.1.57`、PR #58オーナーレビュー修正後）。**旧HEAD `0b7bb88`では実施しない**（レビューで阻止点が確定したHEAD）。修正後HEADがレビューを通ってから実施する。実施者: Codex／専用Windows 11 VM。
設計・残留の実測: [c2c-deletion-design.md](c2c-deletion-design.md)。前提の受入: [windows-acceptance-w-c2.md](windows-acceptance-w-c2.md)。

> **本書は手順書であり、結果ではない。** Cloud工程ではWindows実機・WebView2・実モデルを一切実行していない。
> **破壊系の受入**: 実ユーザーのdata root・既定のユーザー領域・既存データのあるディレクトリ・OneDrive等の同期先の実データでは**実行しない**。
> この受入専用の新しいdata rootと、合成音声・合成出力先だけを使う。音声・転写本文・実パス・tokenを報告やログへ貼らない（件数・状態・固定codeだけ）。

## 0. 目的と合否

| # | 確認 | 合格条件 |
|---|---|---|
| 1 | v7→v8移行 | W-C2で作ったv7のrootを**コピー**して起動 → `user_version`=8、起動前backupが7、0.1.56のbuildでbackupを開ける |
| 2 | ③ 履歴のみの削除 | 確認ダイアログの既定フォーカスが取消。実行後: 行が消える、`GET /jobs/{id}`=410、出力dirとmarker・元音声が**byte同一** |
| 3 | ④ 成果物削除は**全環境で拒否** | 計画が`artifact_deletion_disabled`、「削除する」が押せない。偽tokenでの実行も409 `artifact_deletion_disabled`。dir・ファイル・DBの全行が不変（DELETE 0、ハッシュ不変、`job_deletions` 0件） |
| 4 | 残留の実測 | ③の前後で`operations.sqlite3`本体に合成sentinel（Promptに入れた一意の文字列・入力ファイル名）が残らない。backupには残る。`-wal`ファイルは存在しない |
| 5 | 結果不明・再照合 | 実行中に回線を切る（DevToolsのoffline等）→「結果不明」、再照合で同じ結果、二重削除なし |
| 6 | 既存機能の非退行 | W-C2の非表示/再表示・再試行・成果物ビュー、旧GUI・`--next-ui`の起動が変わらない |

④は**どの環境でも実行されない**（製品にファイル削除のコードが無い）。実ファイル削除の安全性は今回の合否に含めず、「拒否されること・副作用0」だけを確認する。

## 1. 前提

| 項目 | 条件 |
|---|---|
| checkout | 束C2cのDraft PRのHEAD（SHAを記録）。`git status`がclean |
| data root | **この受入専用の新しいroot**。移行確認(#1)にはW-C2のrootの**コピー**を使い、元は残す |
| profile・モデル・B4証拠 | W-C2と同じ手順で、このcheckoutから作り直す |
| 入力 | 合成音声2つ以上。Presetの`initial_prompt`に一意の合成sentinel（例: `C2C-SENTINEL-<乱数>`）を入れる |
| frontend | `frontend`で`npm ci`→`npm run build` |

## 2. 手順

1. **pytest**: `uv run pytest tests/test_c2c_deletion.py -q`。④は全環境で拒否の試験（ファイル呼出し0・ツリー/DB不変）とレビュー再現7件が実行される。symlink/hardlinkを作る11件のparametrizeはWindowsでskip（試験用fixtureがPOSIX前提）。結果の件数（passed/skipped）を記録。
2. **移行(#1)**: W-C2 rootのコピーで`--next-ui-v1`を起動し、`user_version`・backupの版・旧buildでのbackup open（W-C2 §3と同じ手順）を確認。
3. **③(#2,#4)**: 合成Jobを1件成功させる → 出力dirのファイル一覧とSHA-256、DBファイルのsentinel出現数を記録 → 「履歴を削除…」→ 取消が既定フォーカスであることを確認 → 取消 → 再度開いて「削除する」。
   削除後に、ファイル一覧/ハッシュが同一、`GET /api/v1/jobs/{id}`が410、DB本体のsentinel出現数0、backupの出現数>0、`-wal`無しを記録。
4. **④(#3)**: 別の合成Jobで「履歴と成果物を削除…」→ 拒否理由が「成果物の削除は現在すべての環境で無効です…」、確認ボタンが無効、「削除後も残るもの」は表示されない。DB全表とファイルのハッシュが前後で同一。
5. **結果不明(#5)**: ③の実行直前にDevToolsでofflineにする（WebView2で困難ならPlaywright v1をWindowsで実行し`transcription-s5c-deletion.v1.spec.ts`の結果を記録）。
6. **観察のみ（後続の④Windows実装用、合否外）**: 出力dirに、junction（`mklink /J`）、ファイルsymlink（権限があれば）、別のアプリで開いたままのファイル、長いpath、OneDrive配下を作り、④の計画が**いずれも拒否**されること（`artifact_deletion_disabled`）と、副作用が無いことだけを記録する。
7. **非退行(#6)**: W-C2の手順2〜4を短縮実施（非表示/再表示、再試行、成果物ビュー）。

## 3. 報告

- HEAD SHA、Windows build、WebView2版、各#のPASS/FAIL/BLOCKED、pytest/Vitest/Playwrightの件数。
- 実測値はすべて件数・ハッシュの一致/不一致・固定codeで記録する（path・本文を貼らない）。
- 未実施・BLOCKEDは理由を書き、[#22](https://github.com/zawa356/Utteran/issues/22)・[#55](https://github.com/zawa356/Utteran/issues/55)と混同しない。C2c固有の残件は[#56](https://github.com/zawa356/Utteran/issues/56)またはC2c後続Issueへ。
