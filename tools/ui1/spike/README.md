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

各`package.json`は版を完全固定（`--save-exact`）。lockfile・`node_modules`・`dist`・画像はcommitしない（`.gitignore`）。
build toolは3案とも Vite 8.3.5 / TypeScript 6.0.3（`vue-tsc 3.3.14`がTypeScript 7.0.2で起動しないため統一）。

## 再実行

Node 22以上と、Nodeから`require`できるPlaywright（Chromium同梱）が必要。外部CDNは使わないが、`npm install`には
npm registryへの接続が要る。作業はリポジトリ外の複製で行うと`node_modules`が混ざらない。

```bash
cp -r tools/ui1/spike /tmp/ui1-spike
```

```bash
cd /tmp/ui1-spike && for d in vue-primevue vue-vuetify react-mui; do (cd $d && npm install && npm run typecheck && npm run build); done
```

```bash
cd /tmp/ui1-spike && node e2e/run.mjs --shots > report.json
```

`report.json`の`apps.<name>.scenarios[].ok`が全てtrueであること、`nonceCspViolations`が0であることを確認する。

## ライセンス

スパイクのソースは本リポジトリと同じMIT。依存の本番ライセンス集計（`npm ls --omit=dev --all`）は比較文書に記載。
**PrimeVue 5.x（PrimeUI License、license key必須）は使っていない。** 本番採用時は`THIRD_PARTY_NOTICES.md`を更新する。

## 制約

- 実行はLinuxのheadless Chromiumのみ。**Windows WebView2 / pywebviewでは未実行**。
- backendは合成fake。実API・実モデル・実音声は使わない。
