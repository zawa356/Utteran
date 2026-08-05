# Phase 3d 統合受入試験結果

## 結果

- 実行環境: Windows 11 / Intel Core Ultra 7 255H / Intel Arc 140T / `intel` profile
- NVIDIA GPU: なし
- 対象: G系115ケース + P系77ケース = 192ケース
- 初回全件実行: 2026-08-05 11:37:54+09:00〜12:35:18+09:00（57分16秒）
- 是正再実行完了: 2026-08-05 13:03:31+09:00
- ID別最新結果: **177合格 / 0失敗 / 15スキップ**
- P系: **77/77合格**
- スキップ: G13 8件 + G14 CUDA検証7件。すべて`CUDA hardware not present`の理由付き

結果は`output/_acceptance/results-final-full.jsonl`へ1ケース1行で記録した。`output/`はGit除外で、
文字起こし本文は結果へ保存していない。

## 実行範囲

- 長時間グループ: G13/P14を明示的に選択。G13はCUDA不在でskip、P14は3件すべて合格
- 破壊的ケース: 明示的に選択し、CPU/Intel/Vulkan profile更新、Vulkan削除→復元、native
  clean→build、whisper.cpp base削除→再取得、OpenVINO IR生成→削除を確認
- P14: 話者分離なし102.7秒、XPU話者分離あり378.5秒、同条件resume 1.1秒で合格
- 手動項目: `docs/受入試験_手動確認手順書.md`へ分類済み。G12ではParser、read-only menu、
  transcription dry-runを自動確認

## 初回失敗と是正

初回は164合格・20失敗・8スキップだった。P系は初回から77/77合格。旧G系の失敗を次へ分類した。

1. Phase 2固定前提: CPU auto、旧OpenVINO backend、未導入`large-v3`を現行profile/modelへ更新
2. CUDA要件不足: G14 CUDAケースへ`requires: {cuda: true}`を追加
3. 集計不整合: 任意結果パスの`{results}`、環境skip許容、同一job IDの直近batchログ窓を追加
4. Windows PowerShell 5.1: `setup.ps1`のUTF-8 capture/Python stdin probeと、`start.ps1`の
   3引数`Join-Path`を修正
5. fixture集計: Phase 3d長時間測定WAVを通常受入fixture容量から分離し、Phase 3追加分を反映

是正対象を再実行し、ケースIDごとの最新結果に失敗がないことを確認した。実データの本文、固有名、
秘密値は参照・記録していない。
