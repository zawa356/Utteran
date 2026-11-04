# UI-1 same-condition spike (Vue+PrimeVue / Vue+Vuetify / React+MUI)

**調査用の使い捨てスパイク。本番GUIではない。** 本番の`src/utteran_gui/web`とは無関係で、wheel・installerにも含めない。
比較結果と推奨は[docs/architecture/ui1/framework-comparison.md](../../../docs/architecture/ui1/framework-comparison.md)。

## 構成

| path | 内容 |
|---|---|
| `core/core.ts` | 3案が同じファイルを共有するframework非依存コア（共通設定＋override、latest-wins、timeout/409/再試行、`epoch:seq` reducer、合成backend、テスト用hook `window.__spike`） |
| `core/layout.css` | 3案共通のCSS grid（3ペイン23/45/32、1100px未満で2列＋下段） |
| `vue-primevue/` | Vue 3.5.44 + PrimeVue **4.5.5（MIT最終系列）** + @primeuix/themes 2.0.3 |
| `vue-vuetify/` | Vue 3.5.44 + Vuetify 4.3.2 |
| `react-mui/` | React 19.3.1 + @mui/material 9.5.0 + Emotion 11.14 |
| `e2e/run.mjs` | 3案を同じ手順で駆動するPlaywright script（CSP厳格/nonce、7シナリオ、ready時間、JS heap、`--shots`で画面保存） |

各`package.json`は版を完全固定（`--save-exact`）し、**`package-lock.json`もcommitする**（`npm ci`で同一の依存木を再現する）。
`e2e/package.json`はPlaywright 1.63.0を固定する。`node_modules`・`dist`・画像・`report.json`はcommitしない（`.gitignore`）。
リポジトリ直下の`.gitignore`は`*.ts`（MPEG-TS動画）を無視するため、このディレクトリの`.gitignore`で`!*.ts`として
TypeScriptソースを再追跡している。build toolは3案とも Vite 8.3.5 / TypeScript 6.0.3（`vue-tsc 3.3.14`がTypeScript 7.0.2で
起動しないため統一）。

## 再実行（クリーンcheckoutから）

Node 22以上が必要。外部CDNは使わないが、`npm ci`とChromium取得にはnetworkが要る。作業ツリーの複製ではなく、
**commitをcloneした場所**で実行する（未追跡ファイルに依存していないことを確かめるため）。

```bash
git clone --branch <branch> https://github.com/zawa356/Utteran /tmp/ui1-clean
```

```bash
cd /tmp/ui1-clean/tools/ui1/spike && for app in vue-primevue vue-vuetify react-mui; do (cd "$app" && npm ci && npm run typecheck && npm run build) || exit 1; done
```

```bash
cd /tmp/ui1-clean/tools/ui1/spike/e2e && npm ci && npx playwright install chromium
```

```bash
cd /tmp/ui1-clean/tools/ui1/spike && node e2e/run.mjs --out=report.json
```

`run.mjs`の終了コード: **0** = 3案すべてで7/7シナリオok、かつnonce付きCSPで違反0件。**1** = いずれかのシナリオ失敗、
シナリオ数の不足、またはnonce付きCSPでの違反。**2** = ハーネス自体のエラー。失敗内容は標準エラーに`FAIL ...`として出し、
`report.json`の`failures`にも残す。現行CSP（nonceなし）での違反数は比較用の計測値で、合否には使わない。

CIでは`.github/workflows/ci.yml`の独立job `ui1-frontend-spike`が同じ手順（`npm ci`→typecheck→build→E2E）を実行する。

## ライセンス

スパイクのソースは本リポジトリと同じMIT。依存の本番ライセンス集計（`npm ls --omit=dev --all`）は比較文書に記載。
**PrimeVue 5.x（PrimeUI License、license key必須）は使っていない。** 本番採用時は`THIRD_PARTY_NOTICES.md`を更新する。

## 制約

- 実行はLinuxのheadless Chromiumのみ。**Windows WebView2 / pywebviewでは未実行**。
- backendは合成fake。実API・実モデル・実音声は使わない。
