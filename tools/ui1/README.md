# tools/ui1 — UI-1 investigation tools

調査用。本番GUIのassetではない。結果と解釈は[docs/architecture/ui1/](../../docs/architecture/ui1/README.md)。

| path | 用途 |
|---|---|
| `legacy_gui_repro.mjs` | 現行GUI（`src/utteran_gui/web`）を実CSPでChromiumに載せ、`/api/*`だけを決定的mockにして非同期欠陥D-01〜D-07を再現する。`--json`、`--only=<name>`、`--expect-fixed`（再現が残ればexit 1） |
| `spike/` | Vue+PrimeVue / Vue+Vuetify / React+MUI の同条件スパイク（[README](spike/README.md)） |

`legacy_gui_repro.mjs`はNodeから`require("playwright")`できる環境（または`npm root -g`配下のglobal install）で実行する。
```bash
node tools/ui1/legacy_gui_repro.mjs
```
headless Chromiumの結果であり、Windows WebView2実機の合格ではない。
