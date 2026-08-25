# Utteran 0.1.1 — HF Token / セットアップウィザード再修正指示書

作成日: 2026-08-24


着手前に必ず以下を読んでください。

1. `AISTATE.md`
2. `要件定義.md`（GUI の章）
3. `docs/Phase5d事前準備.md`

作業ブランチは `fix/phase5e-gui-settings` を引き続き使用します。
## 1. 今回確認できたこと

`Utteran-0.1.1.zip` の実装を確認した結果、HF Token とセットアップウィザードについて、少なくとも以下の構造的な問題が残っています。

### 問題A — HF Token画面は存在するが、通常導線では条件付きでしか表示されない

対象: `src/utteran_gui/web/app.js`

`wizardModelChoiceNext()` は、`wizard-diarization-toggle` がONのときだけ `/api/token` を確認し、`showWizardStep("token")` を呼んでいます。OFFの場合はToken画面を通らず、そのまま `wizardDownloadModelsAndVerify()` へ進みます。

したがって、HTML上に `wizard-step-token` が存在していても、利用者が「話者分離も使う」を明示的にONにしなければHF Token入力画面は出ません。

現在の回帰テスト `tests/test_gui.py::test_wizard_assets_wire_up_first_run_flow_and_stay_theme_i18n_aware` は、Token画面のHTMLや `saveToken()` 呼び出しがソース内に「存在すること」しか確認しておらず、実際の画面遷移から到達できることを保証していません。

### 問題B — GUIで「保存済み」と確認しても、profile CLIからそのTokenを読めることは確認していない

対象:

- `src/utteran_gui/settings.py`
- `src/utteran/config.py`
- `src/utteran_gui/setup_wizard.py`
- `src/utteran_gui/web/app.js`

`TokenStore.set()` はGUIプロセスからOS keyringへ保存し、同じGUIプロセスから同じslotを再取得できることを確認しています。この改善自体は正しいです。

しかし、その後モデル取得・smoke testを実行するのはGUI本体ではなく、選択profileの `utteran.exe` 子プロセスです。profile CLI側は `KeyringTokenProvider.get_token()` で別プロセスからkeyringを読みます。

現在は、GUI側で保存・再取得できたあとに「実際に選択profileのCLIから同じcredentialを解決できるか」を確認する経路がありません。

このため、GUIは「設定済み（保存後の取得確認済み）」と表示できても、実際のモデル取得側でToken未取得になる可能性を設計上排除できていません。

なお `utteran-gui.exe --diagnose-keyring <json-path>` はGUI配布exeのkeyring import/backend/get/set/deleteを診断できますが、profile CLI側からの読取確認ではありません。今回の問題を閉じるには両プロセス境界の確認が必要です。

### 問題C — Token画面の「続ける」は、Tokenが利用可能かを確認せずモデル取得へ進む

対象: `src/utteran_gui/web/app.js`

`wizard-token-next` は直接 `wizardDownloadModelsAndVerify()` を呼びます。

そのため、話者分離ONでも、Token未入力・保存失敗・profile側で読取不能の状態をToken画面で止めず、pyannoteモデル取得まで進んでから失敗します。

利用者から見ると「Tokenを設定したのにセットアップが完了しない」という形になりやすい実装です。

### 問題D — ウィザード完了判定がプロセスメモリに依存している

対象: `src/utteran_gui/setup_wizard.py`

`complete()` は `_last_successful_smoke_test_profile` が現在のGUIプロセス内でセットされている場合だけ成功します。この値は永続化されません。

また、smoke test成功時点では `setup_wizard_completed_at` は保存されず、完了画面の「文字起こしを始める」を押したときに初めて `/api/wizard/complete` が呼ばれます。

したがって、smoke test成功後〜完了ボタン押下前にGUIが終了・再起動した場合、成功実績は失われます。

### 問題E — 初回判定と中断再開の状態が十分に結び付いていない

対象: `src/utteran_gui/setup_wizard.py`

現在の `status()` は、概ね「profileが1つ以上あり、settings.jsonも存在する」だけで既存インストール扱いにします。`setup_wizard_completed_at` が無くても自動表示しない設計です。

既存0.1.0以前の利用者へウィザードを再表示しないための互換策としては理解できますが、新規セットアップ途中でsettings.jsonが作成されたケースと旧版利用者を区別できません。

Phase 5cで要求されていた「中断後に続きから再開」「途中で閉じても次回起動時に再開を提案する」を満たすには、開始状態と完了状態を明示的に永続化する必要があります。

## 2. 修正方針

### 2-1. HF Tokenステップをウィザードの正式な共通ステップにする

Token画面を「話者分離ONのときだけ存在する隠れた分岐」にしないでください。

推奨フロー:

1. 構成選択
2. profile venv構築
3. モデル・話者分離利用の選択
4. **Hugging Face / 話者分離の準備**
5. モデル取得
6. smoke test
7. 完了

Tokenステップは毎回表示して構いません。

- 話者分離ON: 有効なToken確認を必須にする。Tokenなしで続ける場合は「話者分離なしで進める」へ明示的に切り替える。
- 話者分離OFF: 「HF Tokenは話者分離を使うときに必要。今は後で設定できる」と表示し、Token入力もスキップも可能にする。

少なくとも、利用者がウィザード内でToken設定箇所を探せない状態をなくしてください。

### 2-2. 「keyringへ保存できた」と「実行profileから利用できる」を別々に検証する

GUIの `TokenStore.set()` の保存後再取得確認は維持してください。

そのうえで、profile venv構築後に、選択profileのCLIを子プロセスとして起動し、実効Tokenを解決できるか確認する診断を追加してください。

推奨案:

- CLIへ秘密値を返さない `utteran config token-status --json` 等を追加する。
- 応答例:

```json
{
  "configured": true,
  "source": "keyring",
  "keyring_available": true
}
```

Token本文は絶対に返さないこと。

さらに可能なら、pyannote gated modelについて軽量なアクセス確認を追加し、次を区別してください。

- Tokenなし
- Token無効 / 401
- Tokenは有効だがモデル利用条件未同意 / gated 403
- 利用可能
- ネットワークエラー

既存の `HuggingFaceTokenMissingError`、`HuggingFaceAuthenticationError`、`ModelAgreementError` の分類を再利用してください。

### 2-3. Token画面の「続ける」にpreflightを入れる

話者分離ONの場合、以下が満たされるまでpyannoteモデル取得へ進めないでください。

1. GUI keyring保存を使う場合、保存後再取得が成功している。
2. 選択profile CLIから実効Tokenを解決できる。
3. gated modelへのアクセス確認で「Token無効」「利用条件未同意」を区別して表示できる。

失敗時はToken画面に留め、利用者がその場で修正できるようにしてください。

`wizardShowError()` の汎用エラー画面へ飛ばすだけではなく、Token入力欄・利用条件リンク・再確認ボタンを残した状態でエラーを表示する方が望ましいです。

### 2-4. `--diagnose-keyring` をprofile境界の診断にもつなげる

現行の配布exe診断は残してください。

Windows実機受入では最低限、次の2点を別々に確認してください。

1. `utteran-gui.exe --diagnose-keyring ...` が成功する。
2. 同一OSユーザーで、選択profileの `utteran.exe` が service=`utteran`, username=`huggingface` のcredentialを解決できる。

GUI診断だけ成功してprofile側読取が失敗する場合を検出できるテスト／診断手順を追加してください。

### 2-5. smoke test成功をその場で永続化する

`_last_successful_smoke_test_profile` だけを完了の根拠にしないでください。

推奨は、smoke testがexit code 0で完了した時点で `setup_wizard_completed_at` を永続化することです。完了画面が表示された時点で、バックエンド上も既に「完了」である状態にしてください。

`POST /api/wizard/complete` を残す場合は、冪等な確認APIにするか、既に永続化済みならその値を返すようにしてください。

### 2-6. 新規セットアップ途中と旧版既存環境を区別する

旧版利用者へ突然ウィザードを出さない互換性は維持してください。

そのため、例えば以下を `GuiSettings` に追加してください。

- `setup_wizard_started_at`
- `setup_wizard_completed_at`

初回／再開判定例:

- profileなし: 初回ウィザード
- `completed_at` あり: 完了済み
- `started_at` あり + `completed_at` なし: 中断中として再開を提案
- profileあり + started/completed両方なし: 旧版既存環境として自動表示しない

ウィザードを手動で開始した時点で `started_at` を保存してください。

## 3. 必須テスト

現在の「ソース内に文字列が存在する」テストだけでは不十分です。最低限、以下を追加してください。

### Frontend導線

- 話者分離OFFでもTokenステップへ到達できる。
- 話者分離ONではToken未設定のままpyannote downloadへ進まない。
- 既に設定画面でToken保存済みの場合、ウィザードToken画面で「設定済み」と認識される。
- Token保存後、profile側preflight成功で「続ける」が可能になる。
- Token無効時はTokenエラー、利用条件未同意時は利用条件エラーを別表示する。
- 「話者分離なしで進める」でASR-only setupへ正常に進める。

### Backend / API

- GUI `TokenStore.set()` 成功後、profile側token-statusでもconfiguredになるケース。
- GUI keyring成功・profile keyring失敗を模擬し、ウィザードが失敗を検出するケース。
- Token本文がAPI・JSON・log・例外へ出ない。
- `HF_TOKEN` / `.env` を実効Tokenとして使える場合の扱いを定義し、テストする。
- 401とgated/利用条件未同意を区別する。

### Completion / restart

- smoke test成功直後に完了状態がsettingsへ永続化される。
- smoke test成功後にGUIプロセスを作り直しても完了状態が失われない。
- setup途中で再起動すると再開対象になる。
- 旧版由来の「profileあり・settingsあり・wizard fieldなし」は自動ウィザードを出さない。

### Packaging / Windows実機

- `--diagnose-keyring` が配布版exeで成功する。
- 配布版GUIから保存したcredentialをprofile CLIが同じOSユーザーで取得できる。
- GUIとprofile CLIのservice/usernameが完全一致する。

## 4. 受入シナリオ

### シナリオ1 — 新規、話者分離あり

1. 新規インストールからウィザード開始。
2. profile構築。
3. 「話者分離も使う」をON。
4. HF Token画面が必ず出る。
5. アカウント・利用条件・read token案内が見える。
6. Tokenを保存。
7. GUI keyring再取得成功。
8. profile CLIからもToken解決成功。
9. pyannote model access確認成功。
10. ASR modelとpyannote modelを取得。
11. diarizationありsmoke test成功。
12. 完了画面表示時点で `setup_wizard_completed_at` が保存済み。
13. GUI再起動後にウィザードが自動再表示されない。

### シナリオ2 — 設定画面で先にToken保存

1. 設定画面でTokenを保存。
2. 「設定済み（保存後の取得確認済み）」になる。
3. ウィザードを開く。
4. Tokenステップで既存Tokenを認識する。
5. profile側preflightでも利用可能と確認される。
6. Token再入力なしで話者分離モデル取得・smoke test・完了まで進める。

**このシナリオを今回の不具合の直接回帰テストにしてください。**

### シナリオ3 — Tokenは有効だが利用条件未同意

1. 有効なread tokenを保存。
2. profile側でToken自体は有効と確認。
3. pyannote access確認で利用条件未同意を検出。
4. 「Tokenが無効」ではなく「モデル利用条件に同意してください」と表示。
5. 同意後に再確認し、同じ画面から継続できる。

### シナリオ4 — keyring backend問題

1. GUI keyring backendを利用不能にした環境を模擬。
2. 保存時点で成功表示しない。
3. `HF_TOKEN` / `.env` の代替手段を表示。
4. profile側で実効Tokenが取得できる場合は、その状態を正しく認識して続行できることを確認する。

## 5. 変更履歴の扱い

0.1.1の変更履歴には、既に次の趣旨が記載されています。

- ウィザード内の共通Token入力改善
- 保存後の再取得確認
- keyring診断追加

しかし現状は、画面の到達条件とprofileプロセス境界の検証が不足しています。

次のpatch releaseでは「0.1.1の記述を言い換えるだけ」ではなく、実際に以下を満たしたことを変更履歴へ明記してください。

- ウィザードの通常導線からHF Token設定へ確実に到達できるよう修正。
- GUI keyring保存確認に加え、選択profile CLIから実効Tokenを解決できることをpreflightで確認。
- Token無効・利用条件未同意・keyring利用不能をToken画面内で区別。
- smoke test成功後のウィザード完了状態を永続化し、再起動を挟んでも失わないよう修正。

## 6. 完了条件

- [ ] HF Token画面がウィザードから見つけられない問題が解消している。
- [ ] 設定画面で保存済みのTokenをウィザードが認識する。
- [ ] GUIで保存済み表示になったTokenを、選択profile CLIが実際に利用できることを確認している。
- [ ] 話者分離ONでToken未確認のままモデル取得へ進まない。
- [ ] Token無効と利用条件未同意が区別される。
- [ ] keyring利用不能時に成功表示しない。
- [ ] `HF_TOKEN` / `.env` の代替経路の扱いが一貫している。
- [ ] smoke test成功がプロセスメモリだけでなく永続化される。
- [ ] 再起動後も完了／再開状態が正しい。
- [ ] 旧版既存ユーザーへ不要な初回ウィザードを再表示しない。
- [ ] Token本文が画面・API・log・例外・テスト出力へ漏れない。
- [ ] 関連自動テストが追加され、既存テストを含めて成功する。
- [ ] Windows配布版でGUI→profile CLIのcredential共有を実機確認する。

## 7. 今回の確認結果

手元で以下の関連テストを実行しました。

```text
PYTHONPATH=src python -m pytest -q \
  tests/test_gui.py \
  tests/test_setup_wizard.py \
  tests/test_config.py \
  tests/test_models.py

62 passed
```

この結果は「現状が正しい」ことを意味しません。今回の症状を再現する導線テスト・別プロセスtoken解決テスト・再起動継続テストが存在しないため、現状の不具合を抱えたまま全テストが通っています。

