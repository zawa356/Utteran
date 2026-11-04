# UI-1 理想UIの情報設計（通常モードA / Expertモード C）

更新: 2026-11-04 JST。**設計案であり実装ではない。** 拘束条件は2026-11-04のユーザー合意
（[UI_UX_刷新計画.md](../../UI_UX_刷新計画.md#ui-1で確定したユーザーui要件2026-11-04)）。
backendが未提供の機能は「実装済み」と書かず、[backend-gap-matrix.md](backend-gap-matrix.md)のG番号で示す。

## 1. 全体構造

- **GUI-first**。CLI/coreは実行・意味検証・設定の正本のまま。
- **常設サイドバー**で機能を分離し、1枚の巨大フォームにしない。
- 通常/Expertは**同じデータの別表示**。モード切替・画面移動で入力一覧・共通設定・override・Jobを破棄しない
  （スパイクの`mode-switch-keeps-state`で検証済みの方式: 状態はview外のコアが保持）。

```text
┌ Sidebar ───────────┐
│ ● 文字起こし        │  通常: 入力 → 推奨 → 開始 → 状態 → 結果
│   結果・履歴        │  Expert: 3ペイン（下図）
│   モデル            │
│   キュー  (2)       │  件数バッジは /sync + events 由来（G-04）
│   セットアップ  !   │  未準備時に注意マーク（ready判定はbackend）
│   設定              │
│ ─────────────────  │
│ [通常 | Expert]     │  モード切替（設定に保存、既定=通常）
│ ローカル実行 v0.x   │
└────────────────────┘
```

## 2. 通常モード A（管理ツール型）

```text
┌ 文字起こし ─────────────────────────────────────────────────────────┐
│ 1 入力   [ファイルを追加] [フォルダ]   ここへドロップ               │
│          meeting-a.wav   12:34   ✓ 対応形式                          │
│ 2 構成   推奨: 高精度（自動）            [詳細 ▸]                    │
│          ▸ 詳細: ASR faster-whisper / large-v3-turbo / CUDA 0        │
│            話者分離 pyannote / CUDA 0   根拠: backendのready証拠(G-01)│
│          話者分離 [✓]  言語 [日本語 ▾]  出力 [SRT][JSON][MD]          │
│ 3 開始   [文字起こしを開始]   ← pending中はspinner＋disabled         │
│ 4 状態   ● キュー待ち(2番目) / 実行中 ASR / 完了 / 失敗(理由＋再試行)│
│ 5 結果   [結果を表示] [出力フォルダ]                                 │
└──────────────────────────────────────────────────────────────────────┘
```

- CUDA/Vulkan/OpenVINO/Whisper.cpp等の**選択を強要しない**。推奨構成はbackendの解決結果を表示するだけ。
- 実際の採用backend/device/根拠は`詳細`に折りたたむ（常に確認可能）。
- 準備不足は空欄ではなく「この構成にはセットアップが必要です → [セットアップへ]」を開始ボタンの位置に表示。

## 3. Expertモード C（統合ワークスペース、3ペイン）

```text
┌ Sidebar ┬ 入力 (23) ──────────┬ 処理設定 (45) ────────────────────────┬ 出力・キュー (32) ─────────┐
│         │ [+追加] [フォルダ]   │ ◉ 共通デフォルト  ○ 選択ファイル     │ [選択を投入] [全件を投入]  │
│         │ ☑ meeting-a.wav  ja  │ Preset [会議・日本語 ▾] (B5)          │ ── キュー (paused ⏸) ──   │
│         │ ☐ interview-b  en*   │ ┌ 工程 ───────────────────────────┐ │ 1 meeting-a   実行中 ASR   │
│         │   * = override あり  │ │ 入力 → ASR → 話者分離 → 出力     │ │ 2 interview-b 待機  [↑][↓] │
│         │                      │ └─────────────────────────────────┘ │   [保留] [取消]            │
│         │ 状態列: 未対応/準備中 │ ASR     model [large-v3-turbo ▾]      │ ── 結果 ──                 │
│         │ /投入済み/完了       │         言語  [ja ▾]  [↺ 共通に戻す]  │ meeting-x  完了 [開く]     │
│         │                      │ 話者分離 [✓] 人数 [自動 ▾]            │                            │
│         │                      │ 出力    [SRT][JSON][MD]               │ 接続: ● 同期済み (epoch:seq)│
│         │                      │ 実行経路: faster-whisper/CUDA (ready) │ 再同期中… / 要再読込        │
│         │                      │ Prompt / 詳細引数 [▸]                 │                            │
└─────────┴──────────────────────┴───────────────────────────────────────┴────────────────────────────┘
```

- 左: 入力の追加・一覧・複数選択。各行に**effective設定の要約とoverride印**。
- 中央: Inspectorは「共通デフォルト」と「選択ファイル」の2タブ。選択ファイルでは各項目に
  「共通値を継承 / 上書き」の切替と[共通に戻す]。工程（入力→ASR→話者分離→出力）を固定列で表示し、
  自由接続ノードエディタは作らない。
- 右: 投入、キュー（順序・保留・一時停止・取消）、進捗、結果、接続状態。
- 幅23/45/32は提案。各ペインは独立scroll、ペイン境界はリサイズ・折りたたみ可能（Vuetify採用時は
  CSS grid＋小さなresizer、[framework-comparison](framework-comparison.md#2-uiキットの標準部品とライセンス作業時点の実パッケージと公式情報)）。

### 縮退（狭い窓・高DPI）

| 実効幅 | 配置 |
|---|---|
| ≥1280px | 3ペイン横並び |
| 1024〜1279px | 入力＋処理設定の2列、キューは下段（スパイクの`@media (max-width: 1100px)`で確認） |
| <1024px（DPI 200%で1920px相当の窓など） | 1列＋ペインをタブ切替。sidebarはアイコンのみに折りたたみ |

日英の長い文言でボタンが崩れないよう、ラベルは折返し可能・ボタンは最小幅を持つ。keyboard: sidebar→ペイン順の
Tab順、F6でペイン移動、入力一覧は矢印キー選択、Enterで投入、Escでdialogを閉じる。focus ringを消さない。

## 4. 共通default → override → 検証 → 不変Job Snapshotの責務

| 段階 | 正本 | UIの責務 | backend |
|---|---|---|---|
| 共通デフォルト | B5 Preset/Policy（`/api/v1/presets`、`/execution-policies`）。保存はIf-Match/ETag | 編集・保存、409時は差分表示と再読込 | 存在（opt-in） |
| ファイル別override | **永続化先が未定義**（G-05）。B7投入時のbody内でfile単位に差分を持つ案 | 編集中はUIコアのdraft。未投入のoverrideは保存されないことを明示 | 未提供 |
| 検証 | B5 `/validations`、B7 `/execution-resolutions`（短命HMAC token） | 結果を項目横に表示。frontendは互換表を持たない | 存在（opt-in） |
| 投入 | B7 `POST /jobs`（Idempotency-Key、batch all-or-nothing） | 再試行は同じkey。202受理はterminalではない | 存在（opt-in） |
| 不変Snapshot | B7 job snapshot（private） | 投入後の設定は読み取り専用で表示（「投入時の設定」） | 存在（opt-in） |

## 5. 状態表示（入力〜環境）

| 状態 | 表示 | 根拠 |
|---|---|---|
| 入力未選択 | 開始ボタンdisabled＋「入力を追加してください」 | client |
| ロード中 | 領域ごとのskeleton＋操作disabled（他領域は操作可能） | request phase |
| 失敗 | 領域内のエラー＋[再試行]、詳細コード | APIエラー分類 |
| 未対応 | 入力行に「未対応形式」 | 既存拡張子判定（backend検証が正） |
| 環境未準備 | 「セットアップが必要」＋導線。開始不可 | **B4 ready証拠**（G-01） |
| 再試行中 | 同じIdempotency-Keyで再送中と表示 | client |
| 再同期中 | 右ペイン下「再同期中…」、完了後に`/sync`で置換 | B9 `resync_required`/410 |

## 6. ready判断と「不成立な組合せを表示しない」

- **ready判断はbackend（B4 capability resolver＋B7 execution resolution）が決める**。frontendは互換表を
  複製しない。現行`app.js`の`genaiIsBlocked()`（GenAI×言語）のような判定は新UIでbackendへ移す（G-02）。
- 選択肢は**backendが返した成立候補のみ**を表示。`ready`証拠がない候補は「準備が必要」として区別し、
  「実行可能」と表示しない。現状、標準collectorにはB4 smoke ready証拠がなく、実機Jobは通常
  `409 execution_unavailable`になる（[backend/README](../backend/README.md)）。UIはこれを
  「この環境では未検証のため実行できません → セットアップ」と説明する。
- 公開能力一覧（`GET /capabilities`等）は未実装（G-01）。それまで新UIの構成選択画面は旧`/api/environment`の
  `options`を表示するに留める。

## 7. キュー操作とAPIの粒度

| UI操作 | 旧GUI（既定） | B7 v1（opt-in） | UIの扱い |
|---|---|---|---|
| 一覧 | `GET /api/queue`（1秒polling） | `GET /queue`（ETag）＋SSE `queue_changed` | v1接続時はevents＋`/sync` |
| 順序変更 | なし | `PUT /queue/order`（If-Match CAS） | 409時は最新順序を再取得して再提示 |
| 保留 | なし | `PATCH /queue/items/{id}` | — |
| 一時停止/再開 | なし | `PATCH /queue/control`（`running\|paused`） | activate直後はpaused（明示表示） |
| 取消 | `POST /api/queue/{id}/cancel` | `POST /jobs/{id}/cancel`（協調のみ） | **force cancelは`cancel_mode_unsupported`**。ボタンを出さない |
| 再試行/再実行 | なし | `retry` / `rerun` / `revalidate` | 許可状態のみボタン表示 |
| 進捗率 | 旧SSE `progress` | **配信なし**（progressイベント未実装） | v1では工程と状態のみ表示し、%を推測しない（G-03） |

## 8. 旧画面 → 新画面 対応表（機能欠落の管理）

| 旧（`index.html`） | 新 | 移行段階（[frontend-architecture](frontend-architecture.md#段階移行)） |
|---|---|---|
| `view-settings`（テーマ・言語・既定profile・既定入出力・token・ログ・wizard再起動） | 設定 | S1 |
| `view-workspace` 環境ストリップ・再検出 | 文字起こし（通常）上部＋Expert中央「実行経路」 | S2 |
| `view-workspace` フォーム（入力・出力・batch glob・profile/backend/model/device/language/resume・話者分離・形式） | 通常: 1〜5 / Expert: 入力＋Inspector | S3 |
| `progress-panel` `result-panel` | 通常: 4〜5 / Expert: 右ペイン | S3 |
| `view-queue` | キュー＋Expert右ペイン | S3（旧API）→ S5（v1） |
| `view-models`（一覧・download/verify/delete・OpenVINO IR・進捗・取消） | モデル | S4 |
| `view-history` / `view-viewer`（検索・話者filter・時間filter・統計・再出力・話者名・削除・フォルダ） | 結果・履歴 | S4 |
| `view-wizard`（旧wizard: hardware推奨・token・無人setup） | セットアップ（旧wizard）。B8 v1 Setupは別画面で後続 | S4（旧）/ S5（B8） |
| D&D・file dialog・`report_frontend_error` | host bridge | S0 |
| 共通dialog | キットのDialog＋Toast | S0 |
