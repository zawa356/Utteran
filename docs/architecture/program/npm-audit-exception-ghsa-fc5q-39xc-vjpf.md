# npm audit 例外の根拠 — GHSA-fc5q-39xc-vjpf（picomatch ReDoS、Issue #62）

作成: 2026-11-18 JST。対象: `frontend/`の本番依存監査（CI `UI-2 frontend`の`npm run audit:prod`）。
状態: **owner限定承認済み（2026-11-18〜2026-12-18）**。承認根拠: [PR #64のオーナーリスク受容コメント](https://github.com/zawa356/Utteran/pull/64#issuecomment-6981408931)。承認情報は`frontend/audit-exceptions.json`に記録済み。監査ゲートは該当GHSAだけを例外とし、他のHigh/Criticalを引き続き阻止する。

## 1. 事実

| 項目 | 内容（2026-11-18に確認） |
|---|---|
| 勧告 | [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) / CVE-2026-112366、CWE-1333、High（CVSS v4 8.7、可用性のみ）。npm audit上のsource id `1247710`、range `<=4.0.7` |
| 脆弱な条件 | 勧告本文: 攻撃者が**単一分岐の繰り返しextglobパターン**（例 `+(*)`, `+(*a)`）を**供給**できると、短い文字列との照合で指数的バックトラックが起き、Node.jsのevent loopが止まる。攻撃者が制御するのは**globパターン**であり、固定パターンに対する照合対象文字列だけを制御するシナリオは記載されていない |
| 修正版 | **無し**（勧告の"Patched versions: None"、npm registryの`latest`=4.0.7）。上流の関連PR micromatch/picomatch#194 はOpenで、作者は「セキュリティ修正ではない」と明記。勧告に回避策の記載なし |
| npmの提案 | `npm audit fix --force` = vite 8.3.5 → **6.2.7への破壊的ダウングレード**（Vite 6系への後退。採用しない） |
| 監査に現れる経路 | 本番依存`vuetify@4.3.1`が`vite-plugin-vuetify`を**optional peer**として宣言 → `vite@8.3.5` → `picomatch@4.0.7`、`tinyglobby@0.2.17` → `fdir@6.5.0` → `picomatch`。lockfileではいずれも`devOptional: true`。npmは`--omit=dev`でもoptional peerを本番グラフに含めるため、devDependenciesのviteが検出される |
| 前回まで | 2026-11-17の同じコマンドは0件。lockfileはmainから無変更。勧告データの更新（2026-11-17）で新たに検出された |

## 2. 配布物への影響（実測）

| 配布経路 | 含まれるもの | picomatch |
|---|---|---|
| wheel | `pyproject.toml`の`artifacts = ["src/utteran_gui/web-next/**"]`（Viteのビルド出力） | 含まれない |
| PyInstaller（`packaging/gui.spec`） | `datas`に`utteran_gui/web-next`のみ | 含まれない |
| installer（`packaging/installer.iss`） | GUI dist・pyproject・uv.lock・スクリプト類 | 含まれない（`frontend/node_modules`は対象外、git管理外） |
| ビルド出力 `web-next/` | `index.html`、`assets/index-*.js`、`assets/index-*.css` | **0件**（下の照合） |

照合（2026-11-18、HEAD=本PRの基点main `6532174` + 本PR、`npm run build`後）: `picomatch`・`noextglob`・`nobrace`・`POSIX_REGEX_SOURCE`・
`Expected pattern to be a non-empty string`・`tinyglobby`・`fdir`の各文字列を`web-next/`で検索して**全て0件**。
陽性対照として同じ検索を`node_modules/picomatch`に対して行い、各文字列が検出される（検索方法が有効である）ことを確認した。
実行時のGUIはビルド済みの静的資材をWebView2で読むだけで、Node.js・Vite・picomatchを出荷先で実行しない。

## 3. ビルド・開発経路への影響

picomatchが動くのは開発者とCIのNode.jsプロセスだけ（`vite build`、`vitest`、`vue-tsc`、UI-3-0 prototype E2Eの`vite`開発サーバ）。

| picomatchへ渡るパターン | 出所 |
|---|---|
| `optimizeDeps.entries: ["index.html","preview.html"]`、`test.include: ["src/**/*.test.ts"]` | `frontend/vite.config.ts`（リポジトリのリテラル） |
| Vite/Vitest/tinyglobby内部の既定パターン（`server.fs.deny`の既定等） | 依存パッケージ内の固定値 |
| `import.meta.glob` | **使用なし**（`src`・`e2e`を検索して0件） |
| Playwrightの`testMatch`/`testIgnore` | 正規表現（picomatchを経由しない） |

- 外部から入るのはHTTPリクエストのパス等の**照合対象文字列**で、パターンではない。開発サーバは`127.0.0.1`に限定（`playwright.preview.config.ts`、`npm run prototype`）。
- パターンを変えられるのはリポジトリのファイルを変更できる者だけで、その者はCI/開発環境で任意のコードを実行できる。したがって本ReDoSは新しい権限を与えない（悪化はビルドの停止＝可用性のみ）。
- 残留: 開発者が自分でVite/Vitestに悪意あるパターンを与えた場合の自己DoS。CIではジョブのタイムアウトで止まる。

## 4. 例外の仕組み（`tools/npm_audit_gate.py` + `frontend/audit-exceptions.json`）

- `npm audit --omit=dev --json`を実行し、**全severityの全件**を表示する。high/criticalは、次を全て満たす例外エントリに該当する場合だけ通す。
  1. GHSA id・npm source id・package名・vulnerable range・severityが記録と完全一致（勧告の更新、上流修正によるrange変更、severity上昇で**自動的に無効**）。
  2. lockfileのインストール版が記録（`4.0.7`）の範囲内。
  3. `approved_by`がリポジトリowner（`zawa356`）で、`approval_url`がこのリポジトリのissue/PRコメントまたはレビュー。
  4. 有効期間内: **`created <= 今日(UTC) <= expires`**、期間は最大31日（本件は2026-11-18〜2026-12-18）。`created`が未来のエントリは承認欄が埋まっていても適用しない（`NOT YET VALID`。期間の先送りを防ぐ、PR #64レビューP2-2）。
  5. **適用できる対象はスクリプトに固定**（`ALLOWED_SCOPE`: `GHSA-fc5q-39xc-vjpf`・npm source `1247710`・`picomatch`・Issue #62）。例外ファイルは**最大1件**（`MAX_ENTRIES = 1`）。
     別のGHSA・別source・別package・別issueのエントリや2件目のエントリは、承認欄の有無にかかわらずファイル全体を不正として失敗する（PR #64レビューP2-1）。
     別の勧告に例外が必要になった場合は、JSONへの追記ではなく、スクリプトの対象定数を変える**新しいowner レビュー付きPR**が必要。
- 他パッケージを経由する派生finding（vite・tinyglobby・fdir）は、辿った先の勧告が**全て**例外に該当するときだけ通す。別の勧告を1つでも含めば失敗。
- 例外がどのfindingにも該当しなくなったら失敗（不要になった例外の削除を強制）。npm・ネットワーク・JSONの異常は常に失敗。
- 失敗の握り潰しは使わない（`continue-on-error`・`|| true`・`if: always()`・auditの削除・全severity抑制なし）。CIではauditをUI-2ジョブの**最後の手順へ移動**し、auditが赤でもlint・型・Vitest・build・E2E・wheelの結果が隠れないようにした（ジョブ自体は失敗する）。
- 試験: `tests/test_npm_audit_gate.py`（承認なし・他人の承認・期限切れ・31日超・range/severity/source/GHSA変更・インストール版変更・他のhigh/critical・別勧告を含む派生・別パッケージ・不要な例外・npm障害・不正な例外ファイル・コミット済みファイルの形）。

## 5. ownerの承認手順と再審査

1. 本書と`npm run audit:prod`の出力を確認する。
2. 承認する場合、Issue #62またはPRに承認コメントを書き、そのURLとログインを`frontend/audit-exceptions.json`の`approval_url`・`approved_by`へ記入してcommitする（PRレビュー対象）。承認しない場合は記入せず、CIは赤のまま上流修正を待つ。
3. 期限（2026-12-18）までに再審査する: picomatch/viteの修正版の有無（`npm view picomatch dist-tags`、勧告の"Patched versions"）、勧告の更新、依存経路。修正版が出たら依存を更新して例外を削除する（rangeが変われば例外は自動的に無効になりCIが失敗する）。延長する場合は、**既存の`approval_url`を再利用せず**、ownerが新しい受容コメントを書き、そのURLと新しい`created`（その日以前）・`expires`（31日以内）を記入する（PRレビュー対象）。

## 6. 残留リスク

- 例外期間中、`picomatch@4.0.7`はCI・開発環境で使われ続ける（パターンはリポジトリ管理下、§3）。
- 同じ勧告が別の経路（新しい依存）で現れた場合も、package名・range・severity・インストール版が一致すれば例外が及ぶ。経路の変化はレビューで確認する（依存更新はlockfileの差分としてPRに現れる）。
- `approved_by`・`approval_url`は**文字列の形式だけ**を検証する。リンク先コメントの存在・投稿者・内容（本件・本期間・本勧告への受容か）は検証せず、機械的な本人確認ではない（ネットワーク不要の決定的ゲートのため）。
  ownerがPRのdiffとリンク先を目視で確認することが運用上の前提。
