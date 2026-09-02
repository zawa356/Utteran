# 統合受入試験ハーネス

Phase 1/2のG系とPhase 3のP系を同じ`harness.py`/`cases.json`から実行する。既定実行では
長時間グループ（`G13`/`P14`）と破壊的グループを除外する。Phase 3d R-4で、単なるコマンド
実行器から、将来GUIが「この環境で何が動くか」を判定する基盤へ拡張した。

## CIとの役割分担

GitHub ActionsはLinux/Windowsのモデル不要テスト、lint、型、lockfile、PowerShellのBOM・構文・
実起動、公開treeの衛生を確認する最低限の回帰ゲートである。Vulkan SDK/OpenVINOを使うnative build、
実モデル、GPU、長時間音声、性能、破壊的操作と復元はCIでは実行しない。

実質的な品質保証とrelease判定は本ハーネスが担う。release前は対象hardware上で環境要件を満たす
通常ケースに加え、必要な長時間／破壊的groupを明示選択して実行し、理由のない失敗がないことを
確認する。CI合格だけを実モデル・GPU構成の動作保証として扱ってはならない。

```console
python tools/acceptance/harness.py --list
python tools/acceptance/harness.py --group G4 --group P14
python tools/acceptance/harness.py --include-long
python tools/acceptance/harness.py --include-destructive
python tools/acceptance/harness.py --resume
python tools/acceptance/harness.py --summary output/_acceptance/summary.json
```

`--list`は先頭に`!`が付いたIDが破壊的ケースであることを示す。

実行ファイルは`UTTERAN_ACCEPTANCE_UTTERAN`、成果物rootは`UTTERAN_ACCEPTANCE_ROOT`で上書きできる。
未指定時は現在のprofile環境、project内testdata、実行時の入力一覧から解決する。製品のjob root、
model保存先、既定profile、auto選択は固定せず、製品CLI/config/device検出結果を使用する。

NVIDIA hardwareがない環境のCUDA専用観点は失敗にせず、`requires`メタ情報に基づくスキップとして
報告する。G13/P14は実modelと実音声が必要な耐久groupで既定除外、通常groupは数分〜数十分、
耐久groupは数十分〜数時間が目安（各ケースの`estimated_seconds`も参照）。子process出力はUTF-8で
読み、結果には本文全体でなく先頭/末尾とerror行だけを保存する。

## ケース定義のスキーマ

`cases.json`は`Case`オブジェクトの配列。各項目の意味:

| フィールド | 内容 |
|---|---|
| `id` | 受入試験の番号（`P6-3`等）。追跡性のため維持する |
| `group` | グループ識別子（`--group`で選択に使う） |
| `description` | 何を確認するケースか |
| `command` | 実行するargvのリスト。`{project}`/`{python}`/`{utteran}`/`{testdata}`/`{actual}`/`{acceptance}`/`{jobs}`/`{results}`をプレースホルダとして展開する |
| `expected_exit_codes` | 合格とみなす終了コードの配列（既定`[0]`） |
| `timeout_seconds` | タイムアウト（既定600秒）。超過時はprocess treeを終了し失敗として記録する |
| `environment` | 追加で設定する環境変数（既定`{}`） |
| `minimum_peak_memory_bytes` | 指定時、観測ピークメモリがこの値未満なら不合格にする |
| `measure_vram` | trueならNVIDIA GPU全体のVRAM使用量をbaseline/peakで記録する |
| `requires` | 実行に必要な環境条件（下記）。省略時`{}`（無条件で実行対象） |
| `destructive` | 環境を変更するケースか（既定`false`）。`true`は`--include-destructive`か明示`--group`指定でのみ実行する |
| `estimated_seconds` | 所要時間の目安。省略時`null` |

### `requires`（環境からの実行可否判定）

実行前に`devices --json`と`models list --json`を1回だけ取得し（`fetch_environment`）、
各ケースの`requires`と突き合わせて実行可能かを判定する（`unmet_requirements`）。
実行不能なケースは失敗ではなく`result: "skip"`として理由付きで`results.jsonl`へ記録する。
これが将来GUIの「この環境で何が使えるか」判定の土台になる。

```json
"requires": {
  "profile": "intel",                          // 文字列1つ、または候補のリスト
  "backends": ["whisper-cpp"],                  // devices.backendsで真であること
  "native_variants": ["vulkan"],                // devices.native.variantsで真であること
  "models": ["whisper-cpp:large-v3-turbo-q5_0"],// models list --jsonでinstalled:trueであること
  "cuda": true,                                 // devices.ctranslate2.cuda_device_count > 0
  "xpu": true                                   // devices.pytorch.xpu_available
}
```

`profile`は、ハーネスが`{utteran}`を直接起動する（`run.ps1`を経由しない）ため`UTTERAN_PROFILE`が
未設定になる問題を避けるよう、解決した実行ファイルパス（`.venvs/<os>-<profile>/...`）から
`_profile_from_executable`で推定した値を`devices --json`呼び出し時に注入する。

**既知の制約**: 環境スナップショットは1回の実行で1度だけ取得する。`native clean`/`build`や
`models remove`/`download`など、破壊的ケースが実行中に環境を変えても、同じ実行内の後続ケースの
`requires`判定はスナップショット取得時点の状態を使い続ける（再取得しない）。影響を受ける
ケースを続けて確認したい場合は、破壊的ケースの完了後にハーネスを再実行するか、
`--group`で対象groupだけを絞って実行すること。

## 破壊的ケース

`setup.ps1`のプロファイル作成・削除、`native build`/`clean`、`models download`/`remove`/
`prepare-openvino`/`remove-openvino`など、環境を変更するケースは`"destructive": true`を持つ。

- 既定では実行しない（`--include-destructive`または該当ケースを含む明示`--group`指定でのみ実行）
- 削除系ケースは同じグループ内に復元ケースを対で配置する
  （例: `P1-7a`削除 → `P1-7b`復元、`P3-7a`clean → `P6-7`のmissing確認 →
  `P3-7c`rebuild）。依存する確認caseはID prefixにかかわらず復元sequenceと同じgroupへ置く。
- 実行前に状態を記録し、実行後に復元することを個別ケースの並びで表現する

## スキップの判定方法

1. `--resume`指定時、`results.jsonl`に既出のIDはスキップ（`result`は記録しない、そのまま次へ）
2. `requires`を持つケースは、`unmet_requirements`が空でなければ`result: "skip"`として
   理由（`environment unmet: ...`）付きで記録する
3. いずれにも該当しなければ実行し、`pass`/`fail`を記録する

## 実行結果のサマリー

`results.jsonl`（1ケース1行、既存互換）に加えて、`--summary <path>`で実行全体のサマリーJSONを
出力できる。

```json
{
  "total": 42, "passed": 39, "failed": 1, "skipped": 2,
  "duration_seconds": 812.4,
  "skipped_reasons": { "P8-3": "environment unmet: XPU not available" },
  "failed_ids": ["P6-6"]
}
```

文字起こし本文は`results.jsonl`同様に一切含めない。

### 話者分離の粒度判定

`validate.py intermediate --require-quality`は平均exclusive turn、UNKNOWN率、dominant話者率に加え、
複数話者出力へ次を要求する。

- 1.0 segment/分以上
- 最長出力segmentが音声長の25%以下

3分の正常実測25〜31件と、116.2分の修正前18件（最長52.66%）・修正後859件
（7.39件/分、最長1.00%）から定めた。`G13-06`を含む`--require-quality`ケースへ適用される。

## Python API

CLIから起動するだけでなく、`import`して直接呼び出せる。

```python
import tools.acceptance.harness as harness

summary = harness.run_selected(
    cases_path=harness.DEFAULT_CASES,
    groups={"P6", "P7"},
    include_destructive=False,
)
print(summary.passed, summary.failed, summary.skipped)
```

`run_selected`は`RunSummary`（`total`/`passed`/`failed`/`skipped`/`duration_seconds`/`results`）を
返す。将来のGUIはサブプロセス起動ではなくこの関数を直接呼び出せる。`environment`引数で
`devices`/`models`のスナップショットを注入でき、テストや事前取得済みスナップショットの再利用に使う。

## グループ別の所要時間の目安

- 疎通・監査系（P0/P2/P3の非破壊ケース/P8/P11等）: 数秒〜数十秒
- 実モデル推論を伴う機能ケース（P6/P7/P9/P10）: 数十秒〜数分
- 破壊的ケース（プロファイル更新、ネイティブビルド、モデル取得/変換）: 数分〜数十分
- 耐久グループ（`G13`/`P14`、既定除外）: 数十分〜数時間

各ケースの`estimated_seconds`はこの目安を機械可読にしたもので、正確な実測値ではない。

## 手動確認手順書

対話メニューでの実操作やExplorer起動など、パイプ入力や終了コードだけでは意味のある検証が
できない項目は`docs/archive/受入試験_手動確認手順書.md`にまとめてある。どの項目を手順書に回したか、
どの項目をユニットテストや他ツール（`utteran benchmark`等）に委ねたかも同文書に明記している。
