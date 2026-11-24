# 束 E-A — 案件別 Preset と用語集

開発専用 `--next-ui-v1`。契約は [B5 API](../b5/api.md) と [B5 継承](../b5/inheritance.md)、対象は [#78](https://github.com/zawa356/Utteran/issues/78)。

## Preset

案件名を含む名前で B5 Preset を管理する。E-B からは ID と revision の組を参照できる。複製は新 ID の POST、改名・編集は全設定を送る PUT で同じ ID に revision を追加する。PUT と DELETE の強い If-Match は編集開始時点の revision に対応する。

## 用語集

TXT は最大 1 MiB の UTF-8 を受理し、先頭 BOM はデコード時に取り除く。LF／CRLF／CR で行を分け、空白をトリムする前に各行を検査する。行内・行頭・行末のタブや NUL を含む制御文字があれば、下書きの適用や TXT の取り込みを全体で拒否する。

通常の前後空白をトリムし、空行を除く。小文字で比較して重複の最初の表記を残し、重複行と元の行を表示する。上限は 1,000 語、1 語あたり Unicode 256 コードポイント。上限違反や不正 UTF-8 は全体を拒否する。出力は BOM なしで LF と CRLF を選べる。

## 検証

Windows WebView2 の実ファイルダイアログは未検証。pywebview 6.2.1 は既定の `ALLOW_DOWNLOADS=False` で EdgeChromium のダウンロードをキャンセルする。組み込み画面は TXT 書出不可を表示する。組み込み書出を提供するにはホスト側の独立レビューが必要。[#22](https://github.com/zawa356/Utteran/issues/22) の受入で確認する。

## 操作と障害時の照合

- Normal と Expert の共通設定から管理画面を開く。用語集の下書き、適用済みの編集値、サーバー保存を分ける。TXT 取込は追加／置換のプレビューを経て下書きへ反映する。書出は適用済みの語をブラウザーのダウンロードに渡す。
- 保存後の revision 採用は明示操作。412 では最新 GET と編集内容を表示し、最新を起点にする場合は下書き破棄を確認する。通信断や読めない応答のときは GET を先に行い、最新の revision・名前・設定を照合する。同じ内容でも当該要求の成功は断定できない。PUT／DELETE の再送は行わない。POST の再確認は元の Idempotency-Key と要求本文を保持する。
- 削除後、選択中の旧 revision には欠落表示が出る。旧 revision から別 ID への複製で復旧できる。既存 Job snapshot は保持される。
- Expert のファイル別編集は、操作開始時の編集可能な選択に `/asr/glossary` を設定する。空の用語集は明示的に上書きし、継承に戻す操作は上書き自体を解除する。選択や編集可能状態が変化した場合は適用を拒否する。
- エンジンの用語集能力は `GET /api/v1/engines` から表示し、B5 validation の用語集警告と拒否を表示する。faster-whisper の prompt bias の品質は未検証。適用値は B7 の既存の解決・投入経路で凍結する。プライベートな設定内容は認証済み要求の本文とローカル画面で扱う。URL・ログ・SSE・public DTO の経路は変更していない。

## 試験証跡

実 API の回帰試験で、強い If-Match、412、削除後の過去 revision、B5 警告、B7 の private snapshot 不変、公開応答の非漏洩を確認した。Chromium の E-A 3 件で Normal の TXT 実書出・再読込と B5/B7、Expert の応答喪失と継承復帰、英語の 412 比較を確認した。最終 HEAD と push／PR CI は [Draft PR #79](https://github.com/zawa356/Utteran/pull/79) に記録する。

Linux の初回全体試験ではプローブの終了済み子プロセスが PID 1 に残り、既存の生存判定が失敗した。回帰試験で Linux のプロセス状態を確認し、終了済みのゾンビと実行中の子を区別するよう修正した。製品のプローブ終了処理は同じ。
