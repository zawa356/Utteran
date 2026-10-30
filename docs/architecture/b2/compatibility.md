# 旧CLI・job/resume・出力互換性

## 変更前の根拠

B2のcore/CLI変更前、B1 gate commit `0f80d17`でCLI 19ケースとConfig/hash 4構成を固定した。
`tests/fixtures/b2/legacy_cli.json`はその測定値で、B2後の出力から期待値を作り直していない。

`test_application_golden.py`は既存CLIのrun_pipeline/run_batch境界に同じ合成stubを差し込み、
core Config全体、resume/force/unlock、batch flags、console、JSONL、exit codeを固定期待値と比較する。
モデル/GPU/実音源は不要。timestamp、doneの実測elapsed、既知の合成path separatorだけを正規化する。
エラーやevent、設定値、終了コードを比較対象から削除していない。

| ケース | 比較対象 |
|---|---|
| defaults / TOML / .env / env / CLI precedence | 既存Config.load、backend/model/device、auto language、人数 |
| formats / no-diarization | 出力順・話者分離無効 |
| no-resume / force + force-unlock | runnerへ渡るflags |
| folder + recursive/include/exclude | folder選択・batch summary、partial exit 5 |
| file/folder dry-run | dependency前に対象表示・done 0 |
| fileにfolder flag / missing / invalid Config | 元のerror text/JSONL、exit 2/4/2 |
| model / cancelled / KeyboardInterrupt / unexpected | error→doneを1回、exit 3/130/130/1 |

goldenの共通オプションはquiet+progress-json。Richのlive表示そのものをgoldenに固定してはいないが、
RichProgressReporterとJSON reporterがserviceの同一callbackを受ける追加試験、既存CLI/interrupt試験を実施する。
Windows WebView/実terminalでの描画・Ctrl+C/native停止は別受入。

## 共通serviceの変更

CLIの公開Typer signatureと_cli_overridesは不変。logging/token/モデル取得承認/終了・表示はCLIへ残し、
input/filter prepare、既存cheap dependency preflight、single/batch入口だけをserviceへ委譲する。
PipelineOutcome/BatchSummary/ProgressEvent/CancelToken/UtteranErrorは既存型を使う。
モデルmanager、native/benchmarkのコマンド、JobStore/hash policy、pipeline/ASR/diarization/exportアルゴリズムは変更しない。

## hash・resume・旧データ

- audio/asr/diarization/mergeの旧hash golden 4構成が一致。exportは絶対出力pathを含むためcross-platform固定値から外し、同一runtimeでlegacy Configとadapter Configの**全5stage hash**を一致検証する。
- DTO全体を旧hashへ入れず、既存stage_config_hashes(Config, input_hash)を使う。ASR policy=8 / alignment policy=9、manifest/intermediate version=1は変更しない。
- 実pipeline+合成ASRで、同一jobのresumeは全stage再利用、formats変更はexportのみ、Prompt変更はasr/merge/export、merge設定変更はmerge/exportを再実行する。
- force / no-resumeはASRを再実行。旧manifest reader、merged envelope version 1の確認、旧payloadからPipelineResult復元・再exportを検証する。
- adapter経由とlegacy Config経由で同じ実job ID・設定・成果物となり、2回目は既存jobを再利用する。
- batchの単一shared pool・安定順序・完了skip・partial/decode failure・exit codeは既存試験とservice試験で維持する。

`tests/fixtures/b2/exports.json`は固定合成結果のSRT/VTT/JSON/TXT/MDをbyte（hex）で保存したもの。
capture時にexporters/typesが`0f80d17`のGit blobと同じであることを確認し、BOM/CRLFを含め比較する。
私的な音声/本文は使用していない。golden変更は原因と旧仕様をレビューしてから行い、テストを通すために再生成しない。

## profile互換性と秘密情報

[B1 lock/profile影響](../b1/profile-lock-impact.md)は未解決。B2のlock差分はroot version 0.1.30→0.1.31のみで依存versionを更新しないが、全体hashは変わるため既存全profileが再構築警告になる可能性は残る。profile/manifestの書換え・無条件再構築・検査無効化はしていない。runtime closure fingerprintへの改善はGUI/PowerShellと合わせた別PRが必要。

Promptが合成ASRへ渡ること、通常log/adapterのerror/source reprへ漏れないことを試験する。
**whisper.cppの現行--promptはOS process argvへ露出し得る**。B2で保護を完了したとは扱わない。
worker IPC/受渡し・redaction・profile environment管理はB3以降。token/SDK/model cache等の既存runtime環境参照まで全面変更したわけではない。
