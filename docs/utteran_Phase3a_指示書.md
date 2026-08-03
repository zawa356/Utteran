# utteran — Phase 3a 実装指示

## あなたへの依頼

Phase 3a として、**実行環境（venv）のプロファイル分離**、**Phase 3b に必要な事前調査**、
**whisper.cpp のネイティブビルド機構**を実装してください。

Phase 2 と受入試験は完了しています。着手前に必ず以下を読んでください。

1. `AISTATE.md`
2. `要件定義.md`
3. `README.md`
4. `docs/受入試験報告.md`

作業ブランチは `feature/phase3a-environments` を新規作成して使用します。

---

## 背景

Intel 環境（OpenVINO / Vulkan / Arc iGPU）への対応を進めるにあたり、
以下の制約が判明しました。

**PyTorch は CPU 版・CUDA 版・XPU 版が同一パッケージ名の別ビルドであり、
1つの仮想環境には1種類しか導入できません。**

現在の実装は `uv sync --extra cuda` で同一の `.venv-windows` を上書きする方式のため、
プロファイルを切り替えるたびに数 GB の再ダウンロードが発生します。
また、CUDA 環境と Intel 環境を同時に保持できません。

これを解消するため、**プロファイルごとに独立した venv を持つ構成**へ変更します。

---

## Phase 3a の実装範囲

### 含むもの

- 事前調査（Step 0。**最優先で実施すること**）
- `pyproject.toml` の extras 再編と、uv の conflicting extras / explicit index 設定
- プロファイル別 venv のレイアウトと設定項目
- `setup.ps1` の改修（プロファイルの作成・切替・一覧・削除・移行）
- プロファイル指定の実行手段（`run.ps1` と CLI）
- whisper.cpp のネイティブビルド機構（`utteran native` サブコマンド）
- `devices` のプロファイル横断表示
- 要件定義への「実行環境の分離」章の追加

### 含まないもの

- whisper.cpp を使った実際の文字起こし（Phase 3b）
- ggml モデルのカタログ登録、OpenVINO エンコーダ IR の変換（Phase 3b）
- torch XPU による話者分離（Phase 3c）

**Phase 3a の完了時点では、whisper.cpp のビルドが成功し manifest が生成されるところまでを
確認します。推論の実行は Phase 3b です。**

---

## Step 0 — 事前調査（最優先）

以下は、私（設計側）の知識では確定できない事項です。
**実装より先に調査し、結果を `AISTATE.md` の「事前調査結果」節に記録してください。**

調査は文書を読むだけで終わらせず、**実際に実行して確認**してください。
確認できなかった項目は「未確認」と明記してください。推測を結論として書かないこと。

### 調査項目

**I-1. whisper.cpp のバージョン選定**

- 現在の最新の安定タグを確認する
- 参考実装（`_tmp/` に配置された TranscriptTool）は v1.8.6 / commit `23ee035...` を使用している
- 新しい版を選ぶ場合、後述の I-2 / I-3 が満たされることを確認したうえで選ぶ
- 選定したタグとコミットハッシュを記録する

**I-2. 単語レベルタイムスタンプの取得可否**

Phase 3b の設計を左右する最重要項目です。

- `--dtw` オプションで指定できるプリセット（アライメントヘッド定義）の一覧を確認する
- **`large-v3-turbo` に対応するプリセットが存在するか**を確認する
  存在しない場合、既定モデルの選定に影響するため明確に記録すること
- `--output-json-full`（`-ojf`）の出力構造を、実際に短い音声で生成して確認する
  - トークン単位の時刻がどのフィールドに入るか
  - `--dtw` を有効にした場合と無効の場合で、出力にどのような差が出るか
  - 生成した JSON の**構造のみ**を記録する（実データのテキストは記録しない）
- 日本語音声でトークンがどの粒度に分割されるかを確認する
  （1トークンが何文字程度になるか。`align.py` の単語数ベースの閾値に影響する）

**I-3. Vulkan ビルドの前提条件**

- `-DGGML_VULKAN=ON` のビルドに `glslc`（シェーダーコンパイラ）が必要かを確認する
- 必要な場合、その入手経路を確認する（Vulkan SDK のみか、他の手段があるか）
- 参考実装は `vulkaninfo` の存在で判定しているが、これは実行時ランタイムの確認であり
  **ビルド前提の確認としては不十分**である可能性が高い。実際に検証すること
- 現在の開発機に Vulkan SDK が導入済みかを確認する

**I-4. uv の conflicting extras と explicit index の動作**

- 現在の uv のバージョンで、`[tool.uv] conflicts` と `[[tool.uv.index]] explicit = true`、
  `[tool.uv.sources]` の extra 指定が期待どおり動作するかを、小さな検証用プロジェクトで確認する
- 排他 extras を含む `uv.lock` が単一ファイルで生成できるかを確認する
- 動作しない場合、代替手段（プロファイルごとに別の `pyproject.toml`、
  `requirements.txt` 併用など）を検討し、判断と理由を記録する

**I-5. torch XPU と openai-whisper の依存衝突**

- `intel` プロファイルには torch XPU 版と、IR 変換用の `openai-whisper` が同居する
- `openai-whisper` も torch に依存するため、解決時に CPU 版 torch で
  上書きされないかを確認する
- 衝突する場合の回避策（バージョン制約、`tool.uv.sources` の指定方法）を検討する

**I-6. pyannote 4.0.7 の XPU 動作可否**（Phase 3c の準備。調査のみ）

- torch XPU 環境で pyannote が動作するか、未対応オペレーターがないかを確認する
- 実際に短い音声で試せるなら試し、結果を記録する
- 試せない場合は「未確認」として記録し、Phase 3c の課題とする

**I-7. ディスク使用量の実測**

- 各プロファイルの venv がどの程度のサイズになるかを実測する
- 全プロファイルを作成した場合の合計を記録する
- README に記載する

### 調査結果の扱い

調査の結果、本指示書の前提と異なる事実が判明した場合:

1. `AISTATE.md` に「指示書の前提と異なる点」として記録する
2. 安全側（既存の動作を壊さない側）に倒した実装を選ぶ
3. 設計判断が必要な場合は `要件定義.md` を更新してから実装する

**調査結果を反映せず、本指示書の記述をそのまま実装しないでください。**

---

## Step 1 — extras の再編

現在の extras（`pyannote` / `cpu` / `cuda` / `intel` / `onnx` / `dev`）を、
以下の考え方で再編します。

### 設計方針

**torch のビルド種別を選ぶ extras** と、**機能を追加する extras** を分離します。

| extras | 内容 | 排他 |
|---|---|---|
| `cpu` | CPU 版 torch + pyannote | ○ |
| `cuda` | CUDA 12.6 版 torch + pyannote | ○ |
| `xpu` | XPU 版 torch + pyannote | ○ |
| `whisper-cpp` | cmake（ネイティブビルド用） | |
| `openvino` | openvino、openai-whisper、onnxscript | |
| `onnx` | sherpa-onnx / onnxruntime（Phase 3 以降で使用） | |
| `dev` | 開発ツール | |

`cpu` / `cuda` / `xpu` を `[tool.uv] conflicts` に登録します。

### プロファイル定義

| プロファイル | extras | 想定環境 |
|---|---|---|
| `cpu` | `cpu` | GPU なし |
| `cuda` | `cuda` | NVIDIA |
| `intel` | `xpu`, `whisper-cpp`, `openvino` | Intel CPU / Arc / NPU |
| `vulkan` | `cpu`, `whisper-cpp` | AMD その他（OpenVINO なし） |

`vulkan` プロファイルを設けるのは、AMD GPU 環境を OpenVINO なしで対象に含めるためです。

### 実装

```toml
[tool.uv]
conflicts = [[{ extra = "cpu" }, { extra = "cuda" }, { extra = "xpu" }]]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true

[[tool.uv.index]]
name = "pytorch-xpu"
url = "https://download.pytorch.org/whl/xpu"
explicit = true

[tool.uv.sources]
torch = [
  { index = "pytorch-cpu", extra = "cpu" },
  { index = "pytorch-cu126", extra = "cuda" },
  { index = "pytorch-xpu", extra = "xpu" },
]
```

`torchaudio` など torch と組で配布されるパッケージも、同様の指定が必要か確認してください。

**I-4 の調査結果によっては、この構成が成立しない可能性があります。**
その場合は代替案を採用し、判断と理由を記録してください。

---

## Step 2 — venv レイアウト

### 配置

既定はリポジトリ配下、設定で変更可能とします。

```
<venv_root>/
  win-cpu/
  win-cuda/
  win-intel/
  win-vulkan/
  linux-cpu/
```

- `<venv_root>` の既定値はリポジトリ直下の `.venvs`
- 設定 `[general].venv_dir` および環境変数 `UTTERAN_VENV_DIR` で変更可能
- ディレクトリ名は `<OS識別子>-<プロファイル名>`
- OS を含めるのは、WSL と Windows が同じチェックアウトを共有した際の
  相互干渉を防ぐため（現在 `.venv-windows` で回避している問題の恒久対応）
- `.gitignore` に `.venvs/` を追加する

### 既存環境からの移行

現在 `.venv-windows` と `.venv` が存在します。

- **これらを削除しないでください。**
- 新しい環境が動作することを確認したうえで、README に手動削除の手順を記載します
- `setup.ps1` は旧環境を検出したら、新方式へ移行した旨と旧環境の削除方法を表示します

---

## Step 3 — `setup.ps1` の改修

### パラメータ

```powershell
.\setup.ps1 -Profile cpu|cuda|intel|vulkan   # 作成または更新
            -List                             # 作成済みプロファイルの一覧
            -Remove <profile>                 # 指定プロファイルの削除
            -SetDefault <profile>             # 既定プロファイルの設定
            -SkipFfmpeg
            -VenvDir <パス>
```

### 要件

- 冪等であること
- `-Profile` 指定時、対応する venv が存在しなければ作成し、あれば更新する
- **他のプロファイルの venv に影響を与えないこと**
- 作成後、そのプロファイルで実際に動作確認を行う
  - `cpu` / `cuda`: 既存と同じく両バックエンドの probe
  - `intel`: OpenVINO の初期化、torch XPU の検出
  - `vulkan`: Vulkan ランタイムの検出、ビルド前提（I-3 の結果に従う）
- 動作確認に失敗した場合、成功として表示せず終了コード 1 を返す
- `-Remove` は削除対象と解放される容量を表示し、確認を求める（`-Yes` で省略可）
- `-List` は各プロファイルの状態（存在、サイズ、主要パッケージのバージョン、
  最終更新日時）を表示する
- 完了時に、そのプロファイルで実行するための手順を表示する

### 既定プロファイル

- 既定プロファイルを保持し、都度指定でも上書きできる方式とします
- 保存先は `config.toml` の `[general].default_profile`
- 未設定の場合、作成済みプロファイルが1つならそれを使い、
  複数あれば明示指定を求めるエラーとする

---

## Step 4 — プロファイル指定の実行手段

### `run.ps1`

```powershell
.\run.ps1 transcribe .\input\a.mp4                    # 既定プロファイル
.\run.ps1 -Profile cuda transcribe .\input\a.mp4      # 明示指定
```

- 指定または既定のプロファイルの venv 内の `utteran` を起動する
- 該当 venv が存在しない場合、`setup.ps1` の実行を案内するエラーを返す
- どのプロファイルで実行しているかを起動時に表示する
- 終了コードをそのまま透過する

### `utteran profiles` サブコマンド

```
utteran profiles list       作成済みプロファイルと状態
utteran profiles current    現在実行中のプロファイル
utteran profiles path       venv のルートパス
```

Python 側からプロファイルを切り替えて再実行する機能は**実装しないでください。**
プロセスの再起動を伴い、複雑さに見合いません。切り替えは `run.ps1` の責務とします。

### `start.ps1`

既存の対話フロントに、プロファイルの選択・作成・削除メニューを追加します。

---

## Step 5 — ネイティブビルド機構

whisper.cpp をソースから取得し、複数の構成でビルドします。

### 参考実装

`_tmp/` に配置された TranscriptTool の `src/transcription_tool/native.py` が参考になります。
**ただし、コードをそのまま移植するのではなく、設計の考え方を取り込んで
utteran の構造に合わせて実装してください。**

参考にすべき点:

- whisper.cpp をタグとコミットハッシュで固定し、取得後に検証する
- ビルド結果を manifest に記録し、実行時に解決する
- 同一フラグでビルド済みなら再ビルドしない
- OpenVINO の CMake 設定は、pip の `openvino` パッケージの `get_cmake_path()` から解決する
  （OpenVINO ツールキットの別途インストールを不要にするため）
- ビルドディレクトリのパスを短く保つ
  （Vulkan のシェーダー生成サブビルドが深くネストし、MSVC の FileTracker が
  MAX_PATH で失敗するため。参考実装は `ovvk` のような短縮名を使っている）

### ビルド構成

| 名称 | CMake フラグ | 前提 |
|---|---|---|
| `cpu` | `-DWHISPER_OPENVINO=OFF -DGGML_VULKAN=OFF` | なし |
| `openvino` | `-DWHISPER_OPENVINO=ON -DGGML_VULKAN=OFF -DOpenVINO_DIR=...` | openvino パッケージ、OpenVINO GPU |
| `vulkan` | `-DWHISPER_OPENVINO=OFF -DGGML_VULKAN=ON` | Vulkan（I-3 の結果に従う） |
| `openvino_vulkan` | 上記両方 | 両方 |

`openvino_vulkan` は「エンコーダを OpenVINO、デコーダを Vulkan にオフロード」する構成で、
参考実装の実測（Core Ultra 7 255H + Arc 140T、large-v3、60秒音声、4スレッド）では
最速でした。

| 構成 | 壁時計 | encode |
|---|---:|---:|
| openvino_vulkan | 19.6s | 1.7s |
| vulkan | 21.7s | 7.7s |
| openvino | 50.0s | 13.2s |
| cpu | 144.2s | 103.0s |

### ビルド成果物の配置

**ビルド成果物はプロファイル間で共有します。**whisper.cpp のバイナリ自体は
torch に依存しないためです。

```
<build_root>/
  <platform>/
    src/            whisper.cpp のソース
    cpu/
    ov/
    vk/
    ovvk/
    manifest.json
```

`<build_root>` の既定値は、**パス長の制約から短い場所**を選んでください。
`platformdirs.user_data_dir` 配下は Windows で長くなりがちです。
`~/.utteran/native` のような短いパスを既定とし、
`[general].native_dir` / `UTTERAN_NATIVE_DIR` で変更可能にします。

### manifest の設計上の注意

**参考実装は OpenVINO の DLL ディレクトリを manifest に絶対パスで焼き込んでいますが、
utteran では焼き込まないでください。**

環境を分離した結果、OpenVINO の DLL は各 venv の `site-packages/openvino/libs` にあり、
プロファイルごとに異なります。ビルド成果物は共有されるため、パスを焼き込むと不整合になります。

manifest には実行ファイルのパスと CMake フラグのみを記録し、
**実行時ライブラリのディレクトリは、実行時に現在の環境から動的に解決**してください。

manifest に含める項目:

```json
{
  "schema_version": 1,
  "platform": "win-amd64",
  "whisper_cpp": { "tag": "...", "commit": "..." },
  "built_at": "...",
  "toolchain": { "cmake": "...", "compiler": "..." },
  "backends": {
    "openvino_vulkan": {
      "executable": "...",
      "cmake_flags": ["..."],
      "requires": ["openvino", "vulkan"]
    }
  },
  "errors": { "vulkan": "..." }
}
```

### CLI

```
utteran native build [--variant cpu,vulkan,...] [--force]
utteran native status
utteran native clean [--all | --variant <名前>]
```

- `build` は前提条件を満たす構成のみをビルドし、満たさないものは理由を記録してスキップする
- **ビルドには長時間かかります**（特に Vulkan のシェーダー生成）。
  進捗を表示し、各構成の所要時間を記録すること
- `status` は manifest の内容と、現在の環境で各構成が実行可能かを表示する
- ビルド失敗時、cmake の出力の末尾を含む明確なエラーを出す

---

## Step 6 — `devices` の拡張

環境を分離した結果、`utteran devices` は現在の venv の情報しか取得できません。

- 現在のプロファイル名を明示する
- 作成済みの他プロファイルの一覧を併記する
  （各環境の Python を起動して詳細を取得する必要はない。存在と最終更新のみでよい）
- ネイティブビルドの状態（利用可能な構成）を表示する
- Vulkan の検出を追加する（ランタイムとビルド前提を区別して表示すること）
- `--json` の出力にも上記を追加する。既存のキー構造は壊さず、追加のみとする

---

## 要件定義への追記

`要件定義.md` に「15. 実行環境の分離」を新設し、以下を規定してください。
既存の章番号は変更せず、末尾に追加します。

- プロファイルの定義と対応表
- venv のレイアウトと配置場所の設定項目
- extras の再編内容
- 共有するもの（モデル、ジョブ、設定、ログ、ネイティブビルド成果物）と
  分離するもの（Python パッケージ）の区別
- プロファイル切り替え時にジョブのステージが再計算されること
  （`config_hash` に backend と device が含まれるため。仕様として正しい挙動だが、
  利用者に警告を表示すること）

あわせて、14章「依存関係とインストールプロファイル」を新しい extras 構成へ更新し、
16章としてネイティブビルドの仕様を追加してください。

---

## 完了条件

- [ ] Step 0 の調査項目がすべて実施され、結果が `AISTATE.md` に記録されている
- [ ] `cpu` / `cuda` / `intel` / `vulkan` の各プロファイルが独立した venv として作成できる
- [ ] あるプロファイルの作成・更新が、他のプロファイルに影響しない
- [ ] `uv.lock` が単一ファイルとして生成でき、`uv lock --check` が通る
- [ ] `setup.ps1 -List` / `-Remove` / `-SetDefault` が動作する
- [ ] `run.ps1` で既定プロファイルと明示指定の双方が動作する
- [ ] `utteran profiles list` / `current` / `path` が動作する
- [ ] `utteran native build` で、前提を満たす構成のビルドが成功する
- [ ] `utteran native status` がビルド状態を表示する
- [ ] manifest に OpenVINO の絶対パスが焼き込まれていない
- [ ] `utteran devices` が現在のプロファイルとネイティブビルド状態を表示する
- [ ] 既存の Phase 1 / Phase 2 の機能が、すべてのプロファイルで従来どおり動作する
- [ ] 既存の `.venv-windows` を削除していない
- [ ] `ruff check` と `mypy` が通る
- [ ] モデル不要のテストが通る
- [ ] 4文書が更新されている

### 回帰確認

**環境分離は既存機能を壊しやすい変更です。**
少なくとも `cpu` プロファイルで、以下が従来どおり動作することを確認してください。

- 短い音声の文字起こし（`--no-diarization`）
- 話者分離を含む文字起こし
- レジューム（2回目の全ステージスキップ）
- `models list` / `jobs list` / `config show` / `devices`

受入試験で作成した `tools/acceptance/` のハーネスが再利用できるなら活用してください。

---

## 文書の更新義務

これまでと同様、実装と同じ作業単位で以下を更新してください。

| ファイル | 更新内容 |
|---|---|
| `README.md` | プロファイル別のセットアップ手順、`run.ps1` の使い方、ディスク使用量の目安、旧環境の削除方法、ネイティブビルドの手順と前提条件 |
| `変更履歴.md` | 作業単位ごとに追記 |
| `要件定義.md` | 15章・16章の追加、14章の更新 |
| `AISTATE.md` | 事前調査結果、実装判断、未解決事項 |

`README.md` の「`cpu`、`cuda`、`intel` は相互に切り替えて使用します」という記述は
実態と合わなくなるため、必ず修正してください。

---

## 実装上の注意

### 破壊的変更への配慮

- 既存の venv を削除・移動しない
- 既存のジョブ、モデル、設定ファイルを無効化しない
- 既存の CLI の引数体系を変更しない（追加のみ）
- `uv.lock` の再生成は避けられないが、既存の依存バージョンを不必要に上げない

### ディスク容量

全プロファイルを作成すると 6〜8 GB になる見込みです。
作業前に空き容量を確認し、不足する場合は作成するプロファイルを絞ってください。
その場合、作成しなかったプロファイルは「未検証」として記録します。

### ビルド時間

whisper.cpp の Vulkan ビルドは長時間かかります。
タイムアウトを十分に取り、進捗を表示してください。
ビルド中に応答がないように見えることを README に注記してください。

### テスト

以下はネイティブビルドや venv 作成なしにテストできます。重点的に書いてください。

- プロファイル名から venv パスへの解決
- 既定プロファイルの決定ロジック（未設定、1つ、複数の各ケース）
- manifest の読み書きとスキーマ不一致の扱い
- ビルド構成の前提条件判定（検出結果を注入可能にすること）
- 実行時ライブラリディレクトリの動的解決

実際のビルドを伴うテストは `@pytest.mark.requires_build` で分離してください。

### コミット

- ブランチ `feature/phase3a-environments`
- Step 単位でコミットする
- 調査結果の記録も独立したコミットとする
- `git push` は実行しない
- 実データ（`input/`、`output/` の中身）をコミットしない

---

## 禁止事項

- Step 0 の調査を省略して実装を始めること
- 調査結果を推測で埋めること
- 参考実装（`_tmp/`）のコードをそのままコピーすること
  （設計の考え方を取り込み、utteran の構造に合わせて実装すること）
- OpenVINO の絶対パスを manifest に焼き込むこと
- 既存の venv やジョブ、モデルを削除すること
- `要件定義.md` を更新せずに仕様を変えること
- 動作確認していないプロファイルを「動作する」と報告すること

---

## 不明点があった場合

1. `要件定義.md` に記載があれば従う
2. Step 0 の調査で判明した事実を優先する
3. どちらもない場合、安全側に倒し、判断と理由を `AISTATE.md` に記録する
4. 設計の変更を伴う判断は、実装前に `要件定義.md` へ反映する
