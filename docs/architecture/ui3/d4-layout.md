# D4-A 製品ワークスペース

基点は main `08e594fad42ed7f721ee56cb00652013db36a5d2`、開発版 0.1.63。[基点 CI](https://github.com/zawa356/Utteran/actions/runs/44679273024) は15件成功。[Design Freeze](design-freeze.md) に基づき、開発専用 v1 の画面構成を更新した。

- Normal は左に入力・共通設定・環境、右に確認・投入・現在のジョブ。利用幅が940 CSS px未満なら操作順に縦配置。
- Expert は作業領域の ResizeObserver 実測に応じて3列（936px以上）、2列（688px以上）、1列。2列の右側は②・③の切替。各ペイン内でスクロールできる。
- 区切りはドラッグ、矢印16px、Shift＋矢印64px、Home/End、Enter・ダブルクリックのリセットに対応。F6／Shift＋F6 は①②③を循環し、選んだペインへフォーカスする。合成IMEイベント中は動作させない。
- 幅と表示先は ShellController に保持。v1の2つのワークスペースはページ切替時にキャッシュし、入力・検証・ジョブは同一の AppCore を使う。非表示のペインはフォーカス対象から外し、ページを離れると簡易キューの監視リースと native drop listener を返す。短い窓のサイドバーは rail から一時表示できる。
- プロトタイプと再利用するのは分割幅計算と separator 部品。表示や試作用データの接続は従来どおり各エントリーで管理する。

## 確認

初回の製品 Playwright は main で2件ともレイアウト差により不合格。初回実装では Vue KeepAlive の動的コンポーネントキーが共有されて切替に失敗し、モードごとのキーを指定して3件成功。手順は `frontend/` で `npm run build` 後に `UI2_SCREENSHOTS=1 npm run e2e -- layout-d4`。撮影は合成環境でローカルの `test-results/d4/` に保存する。TypeScript、lint、Vitest 603件、本番 build、依存 audit を Linux で確認。全CIは当該 Draft PR の Checks を参照。

D4-Bで操作・表示密度を調整し、D4-Cで第5案との画像対照を記録する。Windows WebView2／DPI／IME／native picker・D&D は [#22](https://github.com/zawa356/Utteran/issues/22) の実機受入に引き継ぐ。
