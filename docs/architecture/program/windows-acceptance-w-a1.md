# W-A1 — Windows実機受入指示書（束A1: エンジン宣言表と実行契約の一本化）

更新: 2026-11-16 JST（開発版`0.1.52`＝束A1、`0.1.53`＝束A2、`0.1.54`＝束C1）。実施者: Codex／専用Windows 11 VM。設計: [../backend/engine-adapters.md](../backend/engine-adapters.md)。

> **本書は手順書であり、結果ではない。** Cloud工程ではWindows実機・WebView2・実モデルを一切実行していない。
> 実ユーザーのdata root・既定のユーザー領域・既存データのあるディレクトリでは**実行しない**。
> 音声・転写本文・実パス・tokenを報告やログへ貼らない（件数・状態・固定codeだけを書く）。

## 0. 目的と合否

束A1はfaster-whisper経路の**挙動を変えない**リファクタリングである。確認するのは次の2点だけ。

1. PR #48で合格した縦断経路（`faster-whisper / large-v3-turbo / fw-cpu / int8`）が退行していない。
2. workerが実行しない設定は、推論前に固定codeで止まり、別エンジンや旧CLIへ流れない。

GPUは対象外（VMにGPUが無ければCUDAは**未受入**のまま記録する）。

## 1. 前提

| 項目 | 条件 |
|---|---|
| checkout | 対象Draft PRのHEAD（SHAを記録）。PR #48受入時のcheckoutを流用する場合も`git status`がcleanでHEADが一致すること |
| data root | **新しい専用root**（例: `%LOCALAPPDATA%\Utteran-A1-WinVM\data`）。PR #48のrootを再利用する場合は、既存ジョブを壊さないことを確認してから |
| 環境変数 | 親シェルから`UTTERAN_DATA_ROOT`等を継承していないことを確認してから、このシェルだけで設定する |
| profile・モデル | PR #48受入と同じ（`setup.ps1 -Profile cpu`、`large-v3-turbo`一式。VC++ v14 x64再頒布可能パッケージ導入済み） |
| B4証拠 | [b4/smoke-local-acceptance](../b4/smoke-local-acceptance.md)の手順でfw-cpuのsmokeを取り直す（24時間TTL） |
| 入力 | 合成音声（短い無音/トーン、または自作の読み上げ）。実録音を使わない |

## 2. 手順

1. `uv sync --extra dev --extra gui --locked` → `uv run --no-sync pytest -m "not requires_model" -q tests/test_engine_declarations.py tests/test_b7_b3_model_route.py tests/test_worker_runtime.py`
   - 期待: 全件passed（件数を記録）。
2. smokeを実行し、別processの`refresh`でfw-cpuが`ready`になることを確認。
3. `utteran-gui --next-ui-v1`を起動（WebView2）。
4. **正常系**: Preset/Policyを選び（無ければ作成）、合成音声1件を投入。
   - 期待: `queued → running → succeeded`、SRT/JSON/MD各1件、再起動後も履歴に残る。
5. **取消**: もう1件投入し、running中に取消。
   - 期待: `cancelling → cancelled`、成果物0件、次のジョブを投入できる。
6. **拒否系**（どれも推論を開始しないこと）:
   - Presetで話者分離を有効にして投入 → validationに`worker_unsupported`（`/diarization/enabled`）、resolutionは4xxで拒否（codeを記録）。
   - **束A1のHEAD（0.1.52）だけ**: Presetに用語集を1語入れて投入 → `worker_unsupported`（`/asr/glossary`）、resolutionは4xxで拒否（codeを記録）。
   - Presetのbackendを`whisper-cpp`にして投入 → `worker_unsupported`（`/asr/backend`）、resolutionは4xxで拒否（codeを記録）。
   - どの場合も旧`POST /api/jobs`が呼ばれない（Network欄で確認）、profileの`faster_whisper`以外が起動しない。
6b. **束A2のHEAD（0.1.53）だけ — 用語集**（UIに用語集欄はまだ無いので、`PUT /api/v1/presets/{id}`でPresetの`settings.asr.glossary`に
   合成の用語を3語入れた新revisionを作る。DevToolsの`fetch`か付属の開発ハーネスを使い、tokenや実データは使わない）:
   - validationに`worker_unsupported`（`/asr/glossary`）が**出ない**こと、`prompt_quality_unverified`が出ること。
   - 投入して`succeeded`、SRT/JSON/MD各1件。失敗時は固定codeを記録（`unsupported_setting`ならB3が旧版の可能性: profile venvを同じcheckoutから作り直す）。
   - 用語を約500文字分に増やしたrevisionでvalidationに`glossary_may_be_truncated`が出ること（投入は任意）。
   - `GET /api/v1/engines`が200で、faster-whisperの`glossary`が`prompt_bias`、他が`unsupported`であること。
   - **認識結果への効果は合否に含めない**（biasであり保証しない）。観測した場合は所見として書く。
6c. **束C1のHEAD（0.1.54）だけ — 出力ディレクトリ（W-C1）**。設計は[b7/output-directory](../b7/output-directory.md)。
   - 手順4の正常系の後、出力先の直下に`<入力名>_<YYYYMMDD-HHMMSS>_<8桁>`が**1つ**でき、その中にSRT/JSON/MDが**ディレクトリと同じ名前**で
     各1件と`.utteran-job`があること。出力先の直下に成果物が直接置かれていないこと。
   - 出力先に事前に置いた合成ファイル（例: `<入力名>.srt`）の内容が変わっていないこと。
   - 同じ入力をもう一度投入 → 別のディレクトリになること（1回目の成果物の更新日時・内容が不変）。
   - 入力名に日本語・空白・`#`・`&`・括弧を含む合成ファイルで投入 → `succeeded`、ディレクトリ名と成果物名が一致。
   - 深い出力先（合計およそ200文字超のpath）で投入 → 投入時に`output_path_too_long`で拒否されること（ジョブが作られない）。
   - Explorerで名前の並び（入力名→時刻順）と、エクスプローラーの「隠しファイル」設定に関係なく`.utteran-job`が邪魔にならないかを所見として書く。
7. GUIを閉じ、既定起動（引数なし）で旧GUIが従来どおり開き、専用root以外にcontrol DBが作られていないことを確認。

## 3. 報告フォーマット

```
HEAD: <sha>            OS/ビルド: <Windows 11 ...>     GPU: なし/あり(<名称>)
手順1 pytest: <passed>/<failed>/<skipped>
手順2 smoke: passed / failed(<code>)     ready: yes / no(<reason_code>)
手順4 正常系: succeeded / <status>(<error code>)   成果物: srt=<n> json=<n> md=<n>   履歴永続: yes/no
手順5 取消: cancelled / <status>
手順6 拒否: 話者分離=<code>  用語集(0.1.52のみ)=<code>  whisper-cpp=<code>   旧API呼出: なし/あり
手順6b 用語集(0.1.53): validation警告=<codes>  job=<status>(<error code>)  truncated警告: 出た/出ない  /engines: 200/<status>
手順6c 出力dir(0.1.54): dir数=<n> 名前一致: yes/no  既存ファイル不変: yes/no  再投入で別dir: yes/no  特殊文字名: <status>  長path拒否: <code>
手順7 既定起動: 正常 / 異常(<概要>)
未実施・未観測: <CUDA 等>
```

失敗時は、固定code・ジョブ状態・時刻・画面のスクリーンショット（合成データのみ）を付け、転写本文や実パスは含めない。
