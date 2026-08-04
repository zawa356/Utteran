# 統合受入試験ハーネス

Phase 1/2のG系とPhase 3のP系を同じ`harness.py`/`cases.json`から実行する。既定実行では長時間の
G13/P14を除外する。

```console
python tools/acceptance/harness.py --list
python tools/acceptance/harness.py --group G4 --group P14
python tools/acceptance/harness.py --include-long
python tools/acceptance/harness.py --resume
```

実行ファイルは`UTTERAN_ACCEPTANCE_UTTERAN`、成果物rootは`UTTERAN_ACCEPTANCE_ROOT`で上書きできる。
未指定時は現在のprofile環境、project内testdata、実行時の入力一覧から解決する。製品のjob root、
model保存先、既定profile、auto選択は固定せず、製品CLI/config/device検出結果を使用する。

NVIDIA hardwareがない環境のCUDA専用観点は失敗にせずhardware不在として報告する。G13/P14は実model
と実音声が必要な耐久groupで既定除外、通常groupは数分〜数十分、耐久groupは数十分〜数時間が目安。
子process出力はUTF-8で読み、結果には本文全体でなく先頭/末尾とerror行だけを保存する。
