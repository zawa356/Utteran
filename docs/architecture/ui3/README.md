# UI-3-0 — 画面設計と隔離型インタラクティブプロトタイプ

更新: 2026-11-05 JST。開発版 **0.1.43（未リリース）**。Issue [#21](https://github.com/zawa356/Utteran/issues/21)（Epic [#18](https://github.com/zawa356/Utteran/issues/18)）。
前段: [UI-2](../ui2/README.md)（S0基盤、0.1.42）、情報設計の合意: [UI-1 ux-structure](../ui1/ux-structure.md)。

> **これは画面設計のレビュー用成果物で、機能移行ではない。** プロトタイプは合成データだけで動き、
> 文字起こし・ジョブ投入・保存・ファイル操作・通信を一切行わない。Vite dev serverでだけ開け、production build・
> `utteran-gui --next-ui`の`/next/`・既定の旧GUIには含まれない。**ownerの画面レビューと設計承認まで、S1以降の本格移行には着手しない。**

## 目的と範囲

| 範囲内 | 範囲外（禁止・未実施） |
|---|---|
| 通常A／Expert C の配置案比較と推奨案（[screen-design](screen-design.md)） | 実Job投入・実キュー操作・推論・モデルDL・Setup実行 |
| 推奨案の操作できる隔離プロトタイプ（`frontend/preview.html`、`src/preview/`） | S1/S2/S3の本番移植、B9 #37修正、v1 activate・DB migration |
| 状態遷移・コンポーネント分割・境界（[interaction-and-component-map](interaction-and-component-map.md)） | 既定GUI・`/next/`本番画面・`app.py`・CSP・installer/portableの変更 |
| レビュー画像の再現手順・テスト結果・引継ぎ（[review-and-acceptance](review-and-acceptance.md)） | 永続化（G-05/G-06/G-15はowner判断待ち） |
| **第2イテレーション**: ownerメモ#1〜#5・配色・環境mockの反映、リサイズ仕様、コントラスト実測（[iteration-2](iteration-2.md)） | 本番`styles/theme.ts`の変更、実環境検出 |
| **第3イテレーション**: ownerメモ#6〜#14（可読性・説明削減・Expertの流れと簡易キュー・同期表示・共通メニュー・環境・設定・進捗mock）（[iteration-3](iteration-3.md)） | 実ログ・診断ファイル・設定保存、B9 SSEのprogress拡張（G-03は提案のみ） |

## プロトタイプの起動

```bash
cd frontend && npm ci
```

```bash
npm run prototype
```

ブラウザで `http://127.0.0.1:5180/preview.html` を開く（Vite dev server、127.0.0.1のみで待受）。backend・`uv`は不要。
同じdev serverの`/`（`index.html`）は本番UIで、`/api`が無いため読み込みエラーになるが、プロトタイプとは無関係。

画面**下端**の「レビュー用操作（製品UIではありません）」帯（折りたたみ可）で、表示状態（1ファイル・未開始／未選択／実行中／失敗／完了／
複数ファイル・未投入／複数ファイル・処理中／読み込み中／読み込みエラー）、実行環境の例（GPU／CPUのみ／未構成／読み込み失敗、すべて架空）、
処理状況の取得の例（正常／確認に時間がかかる／取得できない）、配色案（緑／青＝UI-2）、テーマ、日英、Expertのペイン比率の例
（22/53/25・30/42/28）、メニューの形（メニューバー／メニューボタン）、初期化を切り替えられる。すべてin-memoryで、再読み込みで初期状態に戻る。

## 現状

- **第3イテレーション（2026-11-05、[iteration-3](iteration-3.md)）**: 可読性の下限（ボタン36px・文字13px以上をE2Eで検査）、上部1行の試作表示、
  通常の内部仕様説明の撤去と置換表示、Expertの①ファイル→②処理設定→③キュー投入（件数一致、右＝投入＋簡易キュー）、同期表示の廃止、
  共通メニュー（ファイル/表示/ツール/ヘルプ）とログ・診断・Aboutのmock、環境画面の単一レイアウト、設定の通常/Expert分離、工程内%・推定残りのmock。
  検証: Vitest 130（うちプロトタイプ53）、UI-2 E2E 10、プロトタイプE2E 35。**owner再レビュー待ち。**
- **第2イテレーション（2026-11-05、[iteration-2](iteration-2.md)）**: ownerの実画面レビューを受け、通常＝topbar 3導線＋1ファイルの
  2カラム1画面、Expert＝上部で折りたためる幅可変の管理sidebar＋ドラッグ/キーボードで幅を変えられる3ペイン（作業領域幅で3→2→1列）、
  Explorer風の高密度入力一覧、作業画面右は要約のみ（キュー＝全件管理、結果と履歴＝完了物）、緑配色案（WCAG AA実測）、架空の環境mock。
  検証: Vitest 116（うちプロトタイプ39）、UI-2 E2E 10、プロトタイプE2E 26。**owner再レビュー待ち。**
- 第1案（以下は履歴）: 推奨案は**通常A1（縦ステップ）**と**Expert C1（3ペイン）**。不採択: A2（`VStepper`ウィザード）、C2（上下分割の表＋Inspector）。
- 実装: `frontend/src/preview/`（38 file、Vuetify標準部品のみ、追加依存なし）。本番コードは`eslint.config.js`・`package.json`の
  scripts・`tsconfig.json`のinclude以外を変更していない。
- 検証（Linux、headless Chromium）: Vitest 91（うちプロトタイプ14）、UI-2既存E2E 10、プロトタイプE2E 19（操作9・レビュー画像/DPI 9・隔離1）。
  詳細と実測値は[review-and-acceptance](review-and-acceptance.md)。
- 1280pxのExpertで左ペインが窮屈だったため、**プロトタイプでのみ**Expert表示中は<1440pxでサイドバーをrailにした（本番は未変更、[§4](screen-design.md#4-幅別の縮退と高dpi)）。

## 後続（ownerの承認後）

[review-and-acceptance §6](review-and-acceptance.md#6-ui-3-s1s5ui-4への引継ぎ)の順序（S1→S2→S3→S4→S5、UI-4）で、画面単位の小PRとして移す。

## 未受入（UI-3-0では確認していない）

Windows WebView2での表示・操作、OS DPI実機（125/150/200%）、native D&D・file dialog、IME（自由入力欄が無い）、実backend接続・
SSE・ready判定、GPU/モデル/推論、起動時間・RAM、installer/portable。CI上のheadless Chromiumの合格はWindows実機の合格ではない。
