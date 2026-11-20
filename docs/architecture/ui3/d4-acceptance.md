# D4-C 画面比較と受入記録

対象は開発専用 `--next-ui-v1`。main 基点は `08e594fad42ed7f721ee56cb00652013db36a5d2`。レビュー順は [D4-A #73](https://github.com/zawa356/Utteran/pull/73) → [D4-B #74](https://github.com/zawa356/Utteran/pull/74) → D4-C。承認基準は [UI-3-0 第5案](design-freeze.md)（[PR #38](https://github.com/zawa356/Utteran/pull/38) の `1f72f28f6fac51cb76e0b5e8aaeda0f2911b2abd`）。

## 再生成

`frontend/` で `npm ci` を実行し、Python 側はリポジトリルートで `uv sync --extra dev --extra gui --locked` を実行する。Playwright Chromium を入れてから、次のコマンドで画面を撮影する。

```sh
npm run build
UI2_SCREENSHOTS=1 npm run e2e -- layout-d4 --output test-results/d4-layout-steps
D4_SCREENSHOTS=1 npm run e2e -- acceptance-d4 --output test-results/d4-product-traces
UI3_SCREENSHOTS=1 npm run e2e:prototype
# 別ターミナルで npm run prototype を起動してから
node scripts/d4-preview-review.mjs
```

製品は FastAPI と合成 worker を同梱の試験サーバーで起動し、試験ごとの専用データルートに接続する。参照画面は Vite の `/preview.html`。画像は Git 管理外の `frontend/test-results/d4-review/`（製品 `product-*.png`／第5案 `preview-*.png`）と `frontend/test-results/ui3-screenshots/`（第5案の既存 `i5-*`）に保存する。合成データだけで撮影する。幅の数値は CSS px。DPR の撮影は Chromium のエミュレーション。

## 第5案との目視比較

撮影対象の製品ソースは `47a84577b3c80ef6357de8400b7fdfaf01025d28`。比較は各画面の情報と操作の位置を確認した。下記は同等のシナリオであり、試作用カタログと製品の B5/B7 データは別の合成データを使用する。第5案の i5-01〜05（セットアップ）、i5-11（用語集）、i5-14（修復・初期化）は、今回の製品実装に対応する操作がないため参照に留めた。

| 画面と条件 | 第5案との差と理由 | 判定 |
|---|---|---|
| Normal 1280×820、ja・青／ライト。640×660、en・青／ダーク | 広幅では左がファイルと設定、右が実行。狭幅では入力から縦に続く。製品では空欄の理由、B5の確認とB7投入を個別に示し、待機中は手動再開を表示する。 | 配置一致。実APIの項目で情報量が増える。 |
| Expert 1280×820、1920×1080 | ①ファイル、②処理設定、③キュー投入が一列。製品の②は保存済み Preset／実行 Policy と入力ごとの差分。③の簡易キューには最初の5件と全件画面への導線がある。 | 配置一致。未提供の詳細設定タブと合成進捗は設けない。 |
| Expert 1100×760、640×660 | 中幅は①＋②／③切替。狭幅は3ボタンで1ペインずつ表示し、キューを下段に積まない。①と②のどちらから開始するかは現在のセッション選択とフォーカスに従う。 | 配置・切替一致。 |
| キュー 1280×820、ja・青／ライト。取得失敗を含む | 左が一覧と絞り込み、右が対象の詳細。製品では実 B7 の待機順位と理由、未提供の工程を表示する。取得失敗時はエラーと再読込があり、キュー操作は無効。 | 配置一致。操作可否は実API応答に従う。 |
| 結果・履歴 1280×820、ja・青／ライト | タブと対象を選ぶ一覧、右の詳細は一致。製品は内部ジョブ、状態理由、検証済み成果物の形式と照合状態を表示する。 | 配置一致。Viewer・任意パス・模擬進捗は後続。 |
| 履歴削除の確認・再実行ダイアログ | 履歴のみ削除の対象、保持される項目、既定フォーカスの取消を表示する。製品の実行可否は計画結果から決まる。Escで閉じて起点へ戻る。再実行は製品の確認画面を記録。 | 確認操作を確認。D3-B1 の外部アクセスは BLOCKED。 |
| 外観・環境・設定 | 青／緑とライト／ダークを独立に確認。製品には保存済み言語・外観、起動中のアクセント、実行環境の観測を表示する。 | 操作を確認。モデル管理・セットアップと強制的な最小窓は後続。 |

## 自動試験

- D4-C の追加は11件。日英・4配色・DPR 1 / 1.25 / 1.5 / 2、長い合成ファイル名、幅から求めた列数、内部の横溢れ、F6 と合成IMEイベント、共有入力、実B5/B7の受付と完了、履歴のみ削除の計画／取消、再実行のフォーカス、接続失敗、簡易／全件キューを確認。
- 初回のローカル確認では製品試験のD3-B長尺ケースとD1-04で30秒上限に達した。D3-Bは再実行成功・入力不一致の拒否・英語の確認操作に分離。D1系は #70 の既存追跡と照合する。原因が同一とは判定していない。D4-C の新規長尺ケースも、投入と確認を分離した。確認試験の API seed で短い Idempotency-Key が400となり、16文字以上の試験用キーに揃えて解消した。
- プロトタイプの初回ローカル全体実行で1件失敗。#17ログ出力のボタン操作中に、共有レイアウトの編集に対する Vite の自動再読み込みが発生した。同じ試験を編集なしで実行し成功。
- Linux と Windows の全ジョブ、push と PR の結果は各 Draft PR の Checks を参照。Windows CI の合格と Windows 実機受入は別の記録とする。

## 安全境界と引継ぎ

- B5 の設定検証、B7 の resolution・原子的 batch・CAS と Idempotency-Key、結果不明時の明示再照合、既存のキュー監視リースを維持する。retry は同一論理ジョブの試行、rerun は現在設定の別論理ジョブ。API、DB、起動・CSP、旧 GUI と CLI の既定経路は既存の回帰試験の対象。
- 画面から操作できる削除は履歴のみ。成果物削除は全 OS で無効（[#59](https://github.com/zawa356/Utteran/issues/59)）。D3-B1 外部アプリ・Explorer 選択は BLOCKED。束 E の作業セット・案件別 Preset・用語集・再出力・Viewer、束 F のモデル管理・セットアップは後続。
- D4 のコードは積み上げ Draft PR でレビューする。Windows 実機の受入は [#22](https://github.com/zawa356/Utteran/issues/22) で引き継ぐ。

| 実機項目 | 環境と手順 | 状態 |
|---|---|---|
| 狭幅・実 DPI | 対象 Draft HEAD を Windows 11 実 WebView2 で起動。OS 125/150/200%ごとに窓を広げ、sidebar 展開/rail、3/2/1ペイン、独立スクロールと splitter/F6、フォーカスを確認 | 未実施 |
| native picker / Explorer D&D | 専用 root で合成ファイルだけを使う。ファイル・フォルダの確定と取消、Explorer から複数のファイルを同時ドロップし、選択件数・表示を確認 | W-12 の既存 BLOCKED を継続 |
| 実 IME | Windows 日本語 IME で入力欄・履歴検索を変換し、変換確定 Enter・F6・Esc と入力保持を確認 | 未実施 |
| 実モデル・GPU・配布 | UI-4 の対応プロファイルで合成音声を投入し、結果と再実行を確認。installer／portable 起動と旧経路への復帰を確認 | 未実施 |
