# Phase 5b GUI 手動確認手順書

## 目的と安全上の注意

結果viewer、仮想scroll、検索、履歴、削除、export-only再生成をWindows native WebViewで確認する。
実録音の本文、検索語、参加者名、入力file名を画面capture、報告、Gitへ転記しない。機能確認には
手書き／合成結果かGit対象外の既存jobを使い、実データをfixtureへコピーしない。

## 事前準備

```powershell
.\setup.ps1 -Profile gui -Yes
.\setup.ps1 -Profile cuda -Yes
.\run.ps1 -Profile cuda jobs list --json
.\gui.ps1
```

GUI環境に`torch`／`faster_whisper`がなく、履歴を読むprofileの`utteran.exe`が存在することを確認する。
結果確認用jobは`merged.json`の`schema_version`が1で、merge stageが完了したものを使う。

## 1. 履歴一覧

1. 「結果と履歴」を開き、入力file名、更新日時、状態、ASR model／device、話者数、job容量が出る。
2. 完了／失敗／破損filterと、新旧、file名、容量のsortを切り替え、一覧が即時更新される。
3. profileを切り替えて再読込できることを確認する。job保存先が共通なら同じ履歴になる。
4. 出力folder操作がExplorerの期待directoryを開く。出力fileが移動済みの場合は明確なerrorになる。
5. dark／light、日本語／Englishの双方で文字、状態、button、borderが判読できる。

## 2. 結果viewerと長時間性能

1. 2時間級（目安1,300 segment）のjobを開き、1秒以内を目標に最初の行が表示されることを確認する。
2. 上部にASRと話者分離それぞれのbackend、model、device、生成日時、音声長、segment数、話者数が
   目立つ形で表示される。古い結果と現在の選択値を混同しないことを確認する。
3. 各行に開始／終了時刻、表示名または内部話者label、本文があり、話者色が統計／filterと一致する。
4. 先頭から末尾へ連続scrollし、引っ掛かりや全件DOM展開がないことを確認する。WebViewの開発者toolを
   利用できる場合、`.transcript-row`が全segment数でなく概ねviewport行数＋overscan 16行に留まる。
5. 話者別の発話時間／割合、平均turn長が表示され、segmentの概数と矛盾しない。
6. word時刻／確度が既定画面へ大量表示されないことを確認する。

2026-08-09の自動計測では、既存の長時間job（`merged.json` 9,236,847 byte、
1,280 segment、23,117 word）を本文非出力で読み、GUI用正規化0.326秒、JSON化0.003秒、
payload 255,336 byteだった。650px viewportの同時DOM行上限はoverscanを含め23行である。
同じ読込みはGUI専用`.venvs/win-gui`のWindows Pythonで0.211秒だった。Windows Edge
headlessでもfrontendが起動し、5つの出力形式UIを動的生成することを確認した。
この値は初期表示1秒目標のserver側回帰として使い、native WebViewでの描画と連続scrollは
上記1、4で別途目視確認する。実データの本文、file名、参加者名は計測出力に含めていない。

## 3. 検索とfilter

1. 合成結果にだけ存在する語を入力し、一致数、全一致highlight、前／次移動を確認する。
2. 日本語IMEで未確定文字を入力し、変換候補を選んでいる間は一致数と行が更新されず、確定後に
   短い遅延を置いて更新されることを確認する。
3. 話者chipを複数ON/OFFし、指定話者だけが残る。開始／終了（分）を指定し、範囲と重なる行だけが残る。
4. filter後の結果を検索し、一致移動が表示中の行だけを対象にする。「絞り込み解除」で全件へ戻る。
5. 設定保存とGUI再起動後に検索語が復元されない。`settings.json`、job log、Web Storageへ検索語や
   本文が追加されていないことを、実内容を報告へ転記せず確認する。

## 4. export-only再生成

1. 結果を開き、出力形式を変更し、Git除外済みの別出力先を指定する。
2. `SPEAKER_00`等へ合成表示名を入力し、「exportのみ実行」を押す。
3. 短時間で完了し、生成file一覧が表示される。CLI側では`executed_stages`が`["export"]`だけである。
4. job manifestのaudio／asr／diarization／mergeのstatus、config hash、artifactが実行前後で不変、
   exportだけが更新される。出力本文には指定した表示名が適用され、`merged.json`の内部labelは不変。
5. viewerを開き直すと表示名が適用される。保存先はGUI `settings.json`でなく同じjob内の
   `presentation.json`だけである。実参加者名で確認した場合、その値を報告へ記載しない。

## 5. schema errorと削除

1. 合成jobのcopyで`merged.json.schema_version`を未対応値へ変え、履歴が破損を示し、viewerが
   対応versionと検出versionを明示する。壊れたsegmentを一部表示してはならない。
2. job削除を押し、対象file名、解放容量、出力fileは削除しない旨が確認dialogへ出る。
3. cancelでは何も消えず、承認後は対象job directoryだけが消え、一覧から消える。外部出力fileは残る。
4. 実行中lockを持つ合成jobの削除が拒否される。他jobやjob root全体が削除されない。

## 自動確認へ委ねる項目

- `jobs list/show --json`のschema、metadata、内部label、未対応schema通知
- export-onlyで上流4 stageが不変、表示名適用、`presentation.json`のjob内保存
- session認証、`Cache-Control: no-store`、GUIのcore非import、shell-free引数配列
- 仮想row実装、IME composition event、Web Storage API不使用、合成fixture限定

これらは`tests/test_jobs.py`、`tests/test_cli.py`、`tests/test_gui.py`で回帰検査する。
