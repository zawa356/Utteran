# utteran — Phase bugfix-b 指示書（CI format gate の復旧と運用整合）

## あなたへの依頼

`main` の GitHub Actions が `ruff format --check src tests tools` で失敗している状態を解消し、
**既存の CI 契約を変更せずに、Linux / Windows CI が再び通る状態へ戻してください。**

この作業は機能追加ではありません。Phase bugfix-a で意図的に対象外とされた既存の formatter 差分を
独立した作業単位として解消し、同じ理由で CI が赤いまま作業完了扱いにならないよう、
既存ドキュメント間の運用上の不整合も最小限是正します。

作業ブランチは以下を新規作成して使用してください。

```text
fix/bugfix-b-ci-format
```

着手前に必ず以下を読んでください。

1. `AISTATE.md`
2. `要件定義.md`
   - 特に 16章「開発規約」
   - 18章「ドキュメント運用ルール」
   - 23章「継続的インテグレーションと品質保証」
   - 24.1章「作業単位ごとのバージョン運用」
3. `README.md`
   - 特に「開発と品質保証」
4. `docs/リリース手順.md`
   - 特に「バージョン更新手順（作業単位ごと）」
5. `.github/workflows/ci.yml`
6. Phase bugfix-a の `AISTATE.md` 記録

---

## 背景

Phase bugfix-a の最終確認では、以下が記録されています。

- `pytest -m "not requires_model"`: 365 passed
- `ruff check`: pass
- `mypy`: pass
- `uv lock --check`: pass
- **`ruff format --check src tests tools`: fail**
- formatter が既存 12 file と `tests/test_gui.py` を再整形対象と判定
- bugfix-a の機能修正と無関係な一括整形を避けるため、そのフェーズでは修正しなかった

この判断自体は、機能バグ修正へ無関係な差分を混ぜないという意味では妥当です。
ただし、その結果として `main` は `要件定義.md` 23章で正式に定義されている
`ruff format --check` CI gate を満たさない状態になっています。

さらに文書を照合すると、次の不整合があります。

- `要件定義.md` 23章:
  `ruff check` / `ruff format --check` / `mypy` / `uv lock --check` を Linux CI の必須検査として定義
- `README.md`「開発と品質保証」:
  `ruff format --check` を開発者向け確認コマンドとして明記
- `docs/リリース手順.md`「バージョン更新手順（作業単位ごと）」:
  日常作業の確認項目が `ruff check` / `mypy` / モデル不要testのみで、
  **`ruff format --check` が明記されていない**

したがって今回の目的は、CI設定を弱めることではなく、

1. 現在の formatter 契約へ追跡コードを合わせる
2. 日常作業の品質確認手順を、既存 CI 契約と矛盾しないよう揃える

ことです。

---

## 現在確認されている CI 失敗

調査時点の `main`:

```text
885fb75262291a731adb6c57a13afde1b121dbe4
```

GitHub Actions の最新 Linux `linux-quality` job は、
`Check formatting` で失敗しています。

Windows `windows-tests` は成功しています。

CI log 上では、以下 13 file が formatter の対象として報告されています。

```text
src/utteran/_device_probe.py
src/utteran/devices.py
src/utteran/logging.py
src/utteran/models/manager.py
src/utteran_gui/hardware.py
src/utteran_gui/logging_runtime.py
src/utteran_gui/operation_queue.py
tests/test_asr_registry.py
tests/test_config.py
tests/test_devices.py
tests/test_gui.py
tests/test_hardware.py
tests/test_operation_queue.py
```

ただし、この一覧を盲信せず、**作業開始時の checkout で必ず再現確認してください。**

`uv.lock` 上の Ruff は調査時点で `0.16.1` です。
CI は `uv sync --extra dev --extra gui --locked` を使用しているため、
まず lock 済み環境で同じ結果になることを確認します。

---

## Step 0 — ベースライン確認

**いきなり `ruff format` を実行しないでください。**

まず現在の状態と失敗範囲を記録します。

### 0-A. Git 状態

以下を確認してください。

```console
git status --short
git branch --show-current
git rev-parse HEAD
```

- 作業開始時に意図しない変更がある場合、それを勝手に破棄しない
- 既存変更がある場合は今回の差分と混同しないよう記録する

### 0-B. CI と同じ依存環境

```console
uv sync --extra dev --extra gui --locked
uv lock --check
```

### 0-C. formatter failure の再現

```console
uv run --no-sync ruff format --check src tests tools
```

以下を記録してください。

- exit code
- formatter が列挙した file 数
- file path
- GitHub Actions の既知 13 file と一致するか

一致しない場合は、**現在の checkout の結果を正**とし、
差異の理由を調査してから進めてください。

---

## Step 1 — 原因と範囲の確認

今回の第一仮説は、

> 実装変更の各作業単位で `ruff check` は実施されていたが、
> `ruff format --check` が必須完了条件として一貫して扱われず、
> formatter 差分が累積した

という運用上の取りこぼしです。

ただし、修正前に最低限以下を確認してください。

1. `uv.lock` の Ruff version
2. `.github/workflows/ci.yml` が lock 済み環境を使っていること
3. `pyproject.toml` の Ruff 設定
4. formatter 対象 file に、意図的に formatter を避ける必要がある生成物等が含まれていないこと
5. formatter failure が OS 差や改行コードだけによるものではないこと

**Ruff の version を古くして通す、CI を緩める、対象 file を exclude する、という方向へ先に進まないでください。**

現在の仕様では `ruff format --check` 自体が正規の CI gate です。

---

## Step 2 — formatter 差分の解消

### 方針

Ruff が現在の lock 済み環境で「再整形対象」と判定した Python file だけを整形してください。

推奨:

1. Step 0 で列挙された file を対象に `ruff format` を実行する
2. その後、repository 全体で `ruff format --check src tests tools` を再実行する

例:

```console
uv run --no-sync ruff format <Step 0 で列挙された file...>
uv run --no-sync ruff format --check src tests tools
```

repository 全体へ `ruff format src tests tools` を実行しても結果的に同じ file だけが変わることを
事前に確認できるなら許容しますが、**無関係な差分を増やさないことを優先**してください。

### 必須確認

整形後に以下を確認してください。

```console
git diff --stat
git diff --check
git diff
```

- formatter による機械的変更以外が混ざっていないこと
- ロジック、条件分岐、定数値、公開 API、設定値を変更していないこと
- テストの期待値を都合よく変更していないこと

可能であれば、変更された Python file について
修正前後の AST が同一であることも確認してください
（位置情報等の attribute は比較対象外）。

**今回の基本方針は「semantic change なし」です。**

もし formatter 適用だけでは CI failure を解消できず、
実装変更が必要だと判明した場合は、そこで原因を記録し、
必要最小限の修正へ切り替えてください。

---

## Step 3 — 運用文書の最小是正

今回、アプリケーション仕様そのものは変更しません。

したがって、原則として:

- `README.md`: **変更不要**
- `要件定義.md`: **変更不要**

です。

両文書は既に `ruff format --check` を CI / 開発品質ゲートとして正しく定義しています。

一方、以下は更新してください。

### 3-A. `docs/リリース手順.md`

「バージョン更新手順（作業単位ごと）」の品質確認を、
既存 CI 契約と矛盾しないよう修正してください。

少なくとも、日常の作業単位でも以下を確認することが分かる記述にします。

```console
uv lock --check
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run mypy
uv run pytest -m "not requires_model"
```

CI 固有の公開履歴監査や Windows PowerShell startup test まで
毎作業単位の必須ローカル手順にする必要はありません。

ただし、**「formatter check が失敗していても、変更箇所と無関係なら完了扱いにできる」**
と読める状態は残さないでください。

### 3-B. `AISTATE.md`

新しい先頭節として Phase bugfix-b の記録を追加してください。

最低限以下を含めます。

- CI が赤くなっていた直接原因
- formatter 対象となった file
- semantic change を行わず整形したこと
- `要件定義.md` 23章では format check が既に正式な CI gate だったこと
- `docs/リリース手順.md` の日常確認項目に format check が抜けていたこと
- 今後、CI 必須ゲートの失敗を残した場合は「完了」ではなく明示的な未完了事項として扱うこと
- 最終検証結果

既存の bugfix-a 記録を改変して歴史を書き換えないでください。
今回の節から、その判断を引き継いで解消したことを記録します。

### 3-C. `変更履歴.md`

今回を独立した作業単位として記録してください。

アプリの挙動変更ではないことが分かるようにしつつ、例えば以下の内容を記録します。

- Ruff formatter と追跡 Python source の不整合を解消し、CI format gate を復旧
- 日常の作業単位の品質確認手順へ `ruff format --check` を明記

---

## Step 4 — バージョニング

`要件定義.md` 24.1章および `docs/リリース手順.md` に従い、
**この作業単位でもパッチバージョンを1つ上げてください。**

```text
0.1.12 → 0.1.13
```

以下を揃えます。

- `pyproject.toml`
- `src/utteran/__init__.py`
- `src/utteran_gui/__init__.py`
- `uv.lock`

`uv.lock` 更新後は差分を確認し、
**project version 以外の依存パッケージが意図せず大量更新されていないこと**を確認してください。

不要な dependency upgrade を今回へ混ぜないでください。

`packaging/gui.spec` と `packaging/installer.iss` の version は
`build.ps1` が `pyproject.toml` から伝播する既存設計を維持し、手動変更しません。

マイナーバージョンは上げないでください。

---

## Step 5 — CI 相当の最終検証

### Linux quality 相当

少なくとも以下を、CI と同じ lock 済み環境で実行してください。

```console
uv sync --extra dev --extra gui --locked
uv lock --check
uv run --no-sync ruff check src tests tools
uv run --no-sync ruff format --check src tests tools
uv run --no-sync mypy
uv run --no-sync pytest -m "not requires_model"
uv run --no-sync python tools/check_powershell_bom.py
uv run --no-sync python tools/public_history_scan.py --worktree --fail-on-findings
uv run --no-sync python tools/public_history_scan.py --json output/public-history-scan-ci.json
```

全て exit 0 であることを確認してください。

`output/public-history-scan-ci.json` は Git 追跡対象へ追加しません。

### Windows tests 相当

Windows 上で作業している場合は、`.github/workflows/ci.yml` の `windows-tests` と同等に、

- `pytest -m "not requires_model"`
- 全追跡 `.ps1` の Windows PowerShell 5.1 Parser API 検査
- `setup.ps1 -List`
- `start.ps1` の終了選択
- `run.ps1 -Profile cpu --help`

の headless-safe startup を確認してください。

### 受入試験について

今回は formatter と開発運用文書だけを変更し、
Python AST / runtime semantics を変更しないことを前提とするため、
**実モデル、GPU、長時間音声、破壊的受入試験は必須としません。**

意図しない semantic change が入った場合はこの前提が崩れるため、
影響範囲に応じた受入試験を追加してください。

### installer build について

今回の変更は runtime / packaging の機能変更ではないため、
**`build.ps1` による installer build は必須としません。**

正式 release を行う場合は、別途 `docs/リリース手順.md` の release gate に従ってください。

---

## Step 6 — GitHub Actions について

この指示書では、従来の安全運用に従い **`git push` は実行しません。**

したがって、Codex がこの作業だけで
「GitHub Actions の remote run が green になった」と断言してはいけません。

完了報告は次の2段階を区別してください。

1. **ローカル CI-equivalent gate: pass**
2. **remote GitHub Actions: user が push 後に確認が必要**

利用者が push / merge した後、GitHub Actions の

- `linux-quality`
- `windows-tests`

が両方 success になった時点で、remote CI の復旧完了です。

---

## 完了条件

以下をすべて満たしてください。

- [ ] `main` 相当の baseline で `ruff format --check` failure を再現した
- [ ] failure 対象 file を記録した
- [ ] 現在の lock 済み Ruff を使用した
- [ ] `.github/workflows/ci.yml` の format gate を削除・緩和していない
- [ ] Ruff が要求した formatter 差分を解消した
- [ ] formatter 以外の semantic change がないことを確認した
- [ ] `ruff format --check src tests tools` が pass する
- [ ] `ruff check` が pass する
- [ ] `mypy` が pass する
- [ ] モデル不要 pytest が pass する
- [ ] `uv lock --check` が pass する
- [ ] PowerShell BOM check が pass する
- [ ] public history worktree scan が blocking finding 0 で pass する
- [ ] Windows環境では CI 相当の PowerShell 5.1 check が pass する
- [ ] `docs/リリース手順.md` の日常品質確認へ formatter check を反映した
- [ ] `AISTATE.md` に bugfix-b の原因・判断・結果を記録した
- [ ] `変更履歴.md` に 0.1.13 の作業内容を記録した
- [ ] version が 0.1.13 へ揃っている
- [ ] `uv.lock` に不要な dependency upgrade が混入していない
- [ ] `git diff --check` が pass する
- [ ] build artifact、log、scan output、実データを commit していない
- [ ] remote CI 未確認なら、その旨を明示している

---

## 実装上の注意

### 今回変更しないもの

原則として以下は変更しません。

- ASR backend の挙動
- 話者分離
- alignment
- model catalog
- device detection
- GUI runtime
- setup wizard
- installer / packaging logic
- CI の検査内容そのもの
- Ruff の設定値
- Ruff version を通すためだけの downgrade
- formatter 対象からの除外設定

### 文書の扱い

`要件定義.md` は仕様の唯一の情報源です。

今回の調査で、既存仕様自体を変更しなければ `要件定義.md` を無理に編集しないでください。
「すべての作業単位で4文書を必ず変更する」のではなく、18章の更新義務に従います。

- `変更履歴.md`: すべての作業単位で更新
- `AISTATE.md`: すべての作業単位で更新
- `README.md`: 利用者向け機能・手順が変わる場合のみ
- `要件定義.md`: 仕様変更がある場合のみ

### 機密データ

- `.env` の内容を読み取らない
- input の実録音を開かない
- transcript 本文を Git へ追加しない
- public history scan の検出値そのものを記録しない
- user 固有 path / email / token を文書や commit messageへ転記しない

### コミット

- ブランチ: `fix/bugfix-b-ci-format`
- Step 単位でコミットしてよい
- 最低でも「formatter復旧」と「文書/version整合」が追跡できる粒度にする
- `git push` は実行しない
- build成果物を commit しない
- `Co-authored-by` / `Signed-off-by` 等、email address を含む trailer を追加しない

---

## 禁止事項

- `ruff format --check` を CI から削除すること
- formatter の対象 file を exclude して見かけ上 green にすること
- Ruff を古い version へ戻して既存差分を正当化すること
- `line-length` 等を CI 回避目的で変更すること
- formatter failure を「今回の変更と無関係」として再び完了扱いにすること
- formatter 作業へ機能リファクタリングを混ぜること
- test を削除・skip して green にすること
- dependency を無関係に更新すること
- README / 要件定義を理由なく書き換えること
- 実モデルや既存 venv、model、job、user settings を削除すること
- `git push`、force push、tag 作成、GitHub Release 公開を行うこと
- remote Actions を実行していないのに「CI復旧完了」と報告すること

---

## 最終報告

作業完了時は、最低限以下を簡潔に報告してください。

1. 原因
2. formatter 対象 file 一覧
3. 変更内容
4. semantic change の有無
5. 文書変更内容
6. version
7. 実行した検証コマンドと結果
8. commit hash
9. remote GitHub Actions が未確認なら、その旨
10. 利用者が次に行うべき操作
