# utteran

[English](README.en.md) | 日本語

utteranは、音声・動画から話者別の文字起こしをローカル生成するデスクトップアプリ／CLIです。
会議・インタビュー・講演を、SRT / VTT / JSON / TXT / Markdownへ出力します。
入力音声をクラウド文字起こしAPIへ送信しません。

> 開発版: `0.1.0`（未release）。直近の公開snapshotは`v0.0.1`です。API・設定は1.0まで変更されます。

## 主な機能

- faster-whisper: CPU / NVIDIA CUDA
- whisper.cpp v1.9.1: CPU / OpenVINO / Vulkan / OpenVINO+Vulkan
- pyannote.audio 4.x話者分離: CPU / NVIDIA CUDA / Intel XPU
- 単一ファイル／folder batch、段階別resume、5形式出力
- profile別venv、model／job／native build管理、device診断
- WindowsデスクトップGUI（進捗・中断、結果閲覧・検索、ジョブ履歴、再出力）
- Windows番号menu (`start.ps1`) と自動化向けCLI
- Windowsインストーラー（管理者権限不要、Phase 5d）

主対象はWindows 10/11、Python 3.11/3.12です。Linuxは副対象で、CIがモデル不要testとimportを
確認します。GPU、native build、実model、長時間処理は対象hardware上の受入試験で保証します。

## インストーラーで始める（推奨・Windows）

Python やuvを知らなくても、これだけで使い始められます。管理者権限は不要です。

1. [GitHub Releases](https://github.com/zawa356/Utteran/releases)から最新の
   `utteran-setup-<version>.exe`をダウンロードする。
2. ダウンロード後、SmartScreenの警告が表示された場合は
   [SmartScreenの警告について](#smartscreenの警告について)を確認して実行する
   （このインストーラーは署名していません。理由は同項目を参照してください）。
3. インストーラーを実行する。ライセンスと、依存ライブラリ・モデルは別ライセンスである旨の
   確認後、インストール先を選べます。
4. インストール完了後、スタートメニューの「utteran」から起動する。
5. 初回起動時は初回セットアップウィザードが、ハードウェア検出・推奨プロファイルの提示・
   環境構築・モデル取得・実動作確認まで案内します（Phase 5c）。**この時点でインターネット
   接続と数GB程度の空きディスク容量が必要です。**インストーラー自体には推論に使う
   ライブラリやモデルを含めていません。

GUIはMicrosoft Edge WebView2ランタイムを使用します。Windows 11には標準搭載されています。
Windows 10で起動時に画面が表示されない場合は、
[WebView2ランタイム](https://developer.microsoft.com/microsoft-edge/webview2/)を導入してください。

アンインストールは「アプリと機能」から行えます。アプリ本体は常に削除され、プロファイル環境
（`.venvs`）・ダウンロード済みモデル・ジョブ履歴・GUI設定・ffmpegは、削除するかどうかを
アンインストール時に個別に確認されます（既定はすべて「削除しない」）。`uv`は他の用途でも
共有される可能性があるため、アンインストーラーの削除対象にはなりません。

開発者・Linux利用者・PowerShellから直接操作したい場合は、次の[開発者向け: PowerShellから
直接使う](#開発者向け-powershellから直接使う)を参照してください。インストーラーと
`setup.ps1`はどちらも最終的に同じ`uv sync`を呼ぶため、混在させても壊れません。

### SmartScreenの警告について

このインストーラーは自己署名していません。自己署名証明書ではSmartScreenの信頼度評価に
必要な配布実績が積み上がらず、警告回避の効果が実質的にないためです（詳細は
[要件定義29.6章](要件定義.md#296-署名について判断と理由)）。将来、実績を蓄積できる
証明機関の署名を導入する可能性はありますが、現時点では未署名です。

「Windows によって PC が保護されました」という青い画面が表示された場合:

1. 「詳細情報」をクリックする。
2. 表示された発行者情報（未署名のため「不明な発行者」等と表示されます）を確認する。
3. 次のセクションのSHA-256で、ダウンロードしたファイルが改ざんされていないことを
   確認できたら、「実行」をクリックする。

**この警告は「危険なプログラムである」ことを意味しません。** Microsoftが実績のある
発行者と未署名/実績の浅い発行者を区別できないために表示されるものです。ソースコードは
このリポジトリで公開されており、ビルド方法は`build.ps1`と`packaging/`以下で確認できます。
不安な場合は警告に従って実行を中止し、[ソースからのセットアップ](#開発者向け-powershellから直接使う)を
利用してください。

### ダウンロードの検証（SHA-256）

各releaseのGitHub Releasesページには、インストーラーのSHA-256ハッシュを掲載しています。
PowerShellでダウンロードしたファイルのハッシュを計算し、一致することを確認してください。

```powershell
Get-FileHash .\utteran-setup-<version>.exe -Algorithm SHA256
```

表示された値がReleasesページ記載の値と一致しない場合は、ファイルを削除し、公式の
[GitHub Releases](https://github.com/zawa356/Utteran/releases)から再ダウンロードしてください。

## 開発者向け: PowerShellから直接使う

PowerShellでrepository直下から実行します。管理者権限は不要です。

```powershell
.\setup.ps1 -Profile cpu
.\run.ps1 models download faster-whisper:large-v3-turbo
.\run.ps1 transcribe .\input\meeting.mp4 --no-diarization
```

番号menuを使う場合:

```powershell
.\start.ps1
```

GUIを使う場合は、軽量なGUI環境だけを構築して起動すれば、初回セットアップウィザードが
ハードウェアを検出し、推奨profileの構築・uv導入・モデル取得・トークン設定・実動作確認まで
案内します（Phase 5c）。

```powershell
.\setup.ps1 -Profile gui
.\gui.ps1
```

推論用profile（`cuda`／`intel`／`vulkan`／`cpu`）を手動で構築する場合は、下記の
[Install profile](#install-profile)を直接実行しても構いません。ウィザードは
`setup.ps1`を呼び出すだけなので、どちらの手順でも同じ結果になります。

GUI環境`.venvs/win-gui`はFastAPI／pywebview専用で、PyTorchとfaster-whisperを含みません。
`utteran_gui`は推論coreをimportせず、選択したprofileの`utteran` CLIだけを子processとして起動します。
GUIは結果の仮想スクロール、全文検索、話者／時間フィルタ、モデル・device情報、話者統計、
ジョブ履歴、個別削除、形式・話者表示名・出力先を変えた再生成に対応します。再生成はジョブ内の
`merged.json`からexportだけを実行し、ASRや話者分離を再実行しません。

テーマは既定でWindowsのライト／ダーク設定に追従し、設定画面からライトまたはダークへ固定
できます。既に明示保存されているテーマはそのまま尊重されます。

話者分離にはpyannote modelの利用条件への同意とHugging Face tokenが必要です。取得前に必ず
[ライセンスとモデル利用条件](#ライセンスとモデル利用条件)を確認してください。ウィザード内で
アカウント作成、モデル利用条件への同意、read token発行の各ページを開き、そのままマスク入力欄へ
保存できます。保存後はOSキーリングから実際に再取得できることを確認し、値自体は再表示しません。

## Install profile

```powershell
.\setup.ps1 -Profile cpu
.\setup.ps1 -Profile cuda
.\setup.ps1 -Profile intel
.\setup.ps1 -Profile vulkan
.\setup.ps1 -Profile gui
.\setup.ps1 -List
.\setup.ps1 -SetDefault intel
```

「どのハードウェアか」ではなく「選ぶとどう動くか」を基準に選んでください。特に**話者分離が
GPUで動くかどうか**は、`intel`と`vulkan`の間で処理時間に最も大きく影響します。

| Profile | 対象ハードウェア | 文字起こしの高速化 | 話者分離のGPU実行 | 主な依存 |
|---|---|---|---|---|
| `cuda` | NVIDIA GPU | ○（CUDA） | ○（CUDA） | CUDA 12.6 PyTorch、faster-whisper、pyannote |
| `intel` | Intel GPU（Arc／内蔵GPU） | ○（OpenVINO／Vulkan） | ○（XPU） | XPU PyTorch、OpenVINO、whisper.cpp |
| `vulkan` | AMD等のGPU、またはNVIDIA/Intel以外 | ○（Vulkan） | ×（CPUで実行） | CPU PyTorch、Vulkan whisper.cpp |
| `cpu` | GPUなし | ×（CPUで実行） | ×（CPUで実行） | CPU PyTorch、faster-whisper、pyannote |
| `gui` | （GUI専用、推論しない） | — | — | FastAPI、Uvicorn、pywebview（推論依存なし） |

Intel製CPUでも専用GPU（Arc等）を積んでいない場合は`intel`ではなく`cpu`を選んでください。
`intel`は「CPUがIntel製」ではなく「Intel GPU（Arc／内蔵GPU）を積んでいる」ことが基準です。
どちらか判断が難しい場合や、迷わず選びたい場合はGUIの初回セットアップウィザードが
ハードウェアを自動検出して推奨profileを提示します。

各venvは`.venvs/<os>-<profile>`へ分離されます。model、job、native buildはprofile間で共有します。
`setup.ps1`は依存、ffmpeg、device診断だけを担当し、modelを暗黙downloadしません。

PowerShell 5.1では全`.ps1`のUTF-8 BOMが必須です。BOMを除去するformatterをかけないでください。

### Linux

```console
uv sync --extra cpu
sudo apt install ffmpeg                # Debian / Ubuntu
uv run utteran devices
uv run utteran transcribe audio.wav --no-diarization
```

Intel profile相当は`--extra xpu --extra whisper-cpp --extra openvino`、Vulkan profile相当は
`--extra cpu --extra whisper-cpp`です。`cpu`/`cuda`/`xpu` extrasは同時指定できません。

## Modelとtoken

```console
uv run utteran models list --available
uv run utteran models download
uv run utteran models verify
uv run utteran models path
```

pyannote community-1を使う場合:

1. [model page](https://huggingface.co/pyannote/speaker-diarization-community-1)で条件に同意する。
2. read tokenを発行する。
3. `HF_TOKEN`環境変数、repository直下の`.env`、OS keyringのいずれかへ保存する。

```powershell
$env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxxxxx"
.\run.ps1 models download pyannote:pyannote/speaker-diarization-community-1
```

tokenを`config.toml`、command line、issue、logへ書かないでください。`.env`はGit除外され、runtimeの
log／例外はtoken形式をmaskしますが、漏えい時は履歴修正より先にtokenを失効してください。
GUIから保存したtokenはOS keyringだけに格納され、画面やAPIへ値を返しません。

## 文字起こし

```console
uv run utteran transcribe meeting.mp4
uv run utteran transcribe interview.wav --num-speakers 2 --format srt,vtt,json,txt,md
uv run utteran transcribe lecture.m4a --no-diarization
uv run utteran transcribe recordings/ --recursive --include "**/*.wav"
uv run utteran transcribe meeting.mp4 --progress-json --quiet 2> progress.jsonl
```

`--progress-json`はGUIや自動化向けに、schema version付きUTF-8 JSONLをstderrへ出力します。
進捗にはstage、比率、生成path、終了codeを含みますが、認識segment／単語／文字起こし本文は含みません。

既定でresumeは有効です。設定変更時は影響stage以降だけを再実行し、出力形式だけの変更はexportだけを
やり直します。`--force`は全stage再実行、`--no-resume`はcache不使用です。batchは個別失敗後も
継続し、一部失敗はexit 5、全件失敗は1、Ctrl+Cは130を返します。

同じ入力・設定・出力先で成果物が残っている再実行は、全stageと既存fileを再利用します。上書きも
連番fileの追加も行いません。成果物を削除した場合や出力先だけを変えた場合はexportだけを実行し、
既存の同名fileと衝突するときだけ連番を付けます。完了時には実際のASR model／deviceと、今回
実行したstage／cacheから再利用したstageを表示します。`--asr-model`を別モデルへ変えるとASR以降を
再実行しますが、`large-v3`と`faster-whisper:large-v3`のような同一backend内の別記法は同一modelとして扱います。

Git checkout内へ出力する場合は、`.gitignore`対象の`output`、`transcripts`、または
`utteran-output` directoryを指定してください。JSON / TXT / Markdownを通常の文書directoryへ
出力しようとすると、誤commit防止のため実行を拒否します。repository外の出力先は制限しません。

ASRと話者分離のdeviceは別に指定できます。

```console
uv run utteran transcribe meeting.wav --asr-backend whisper-cpp \
  --asr-device openvino_vulkan --diarization-device xpu:0
```

### 話者分離のメモリ管理

pyannoteのメモリは固定的な基礎量が大きく、音声を短くしても基礎量は減りません。Phase 3dの
単一Intel環境では、XPUが約4.80 GiB + 0.0087 GiB/分、CPUが約2.42 GiB + 0.0057 GiB/分でした。
CUDAはGTX 1070 Tiの139分1点（7.31 GiB）だけで、傾向を推定できません。いずれも保証値ではなく、
他のhardware、driver、model、同時実行processでは外れます。

既定の`memory_guard = "auto"`は、CUDAなら空き専用VRAM、XPUならGPU上限と空きsystem RAMの
小さい方、CPUなら空きsystem RAMから安全率を差し引きます。推定が予算に近い場合、deviceも
`auto`なら安全と確認できたCPUへ退避します。明示deviceは変更せず警告し、基礎量すら入らない
場合は処理前に停止します。取得APIが使えない場合は「不明」と警告し、十分とはみなしません。
実行中のOOMもautoではCPUへ1回だけ再試行し、音声抽出とASR中間結果を再利用します。

```toml
[diarization]
memory_guard = "auto"       # auto | warn | off
memory_safety_margin = 0.0  # 0ならCUDA 10% / XPU 30% / CPU 20%
```

実測peakは音声長と構成・日時だけをprofile共通領域へ蓄積し、同一構成3点以上かつ5分以上の
音声長spanが得られてからローカル式を
優先します。音声名、path、認識内容は保存しません。

```console
uv run utteran memory show
uv run utteran memory reset
```

pyannote 4.0.7には安定したチャンク処理APIがなく、全体clusterとexclusive diarizationを保った
分割には非公開内部への強い依存が必要なため、Phase 4bでは自動分割を実装していません。
CPU基礎量も入らない場合は`--no-diarization`または入力fileの事前分割を使用してください。

## whisper.cppとbenchmark

```console
uv run utteran native build
uv run utteran models download whisper-cpp:large-v3-turbo-q5_0
uv run utteran models prepare-openvino whisper-cpp:large-v3-turbo-q5_0 --device GPU
uv run utteran transcribe meeting.wav --asr-backend whisper-cpp
```

VulkanとOpenVINO+Vulkanの順位は音声長で変わります。Intel Arc 140Tで、180秒ではVulkan
15.363秒／OpenVINO+Vulkan 19.638秒だった一方、24分46秒では110.730秒／65.141秒と逆転しました。
単一環境・素材の観測であり、他環境へ一般化はできません。

既定benchmark長は指定WAV全体です。15分以上を推奨し、短い測定には上記の逆転を示す警告が出ます。

```console
uv run utteran benchmark --audio long-sample.wav \
  --durations 180,900,full --variants vulkan,openvino_vulkan \
  --json benchmark.json
uv run utteran benchmark --audio long-sample.wav --apply
```

`benchmark`は認識本文やjobを保存しません。複数長の`--apply`は最長結果を採用し、測定秒数も
configへ記録します。OpenVINO構成は事前にencoder IR生成が必要です。`auto`はIR未生成でも動く
Vulkanを現在優先します。

## 設定と管理

```console
uv run utteran devices --json
uv run utteran profiles list --json
uv run utteran native status --json
uv run utteran jobs list
uv run utteran jobs list --json
uv run utteran jobs show <job_id> --json
uv run utteran jobs export <job_id> --format txt,json \
  --output-dir transcripts --speaker-label SPEAKER_00=田中
uv run utteran jobs clean --job-id <job_id>
uv run utteran config init
uv run utteran config show
```

一般設定の優先順位は`CLI 引数 > 環境変数 > .env > config.toml > 既定値`です。
トークンの参照元は環境変数、`.env`、OS キーリングの3段階で、この順に優先します。全設定、終了code、JSON schema、resume hashは
[要件定義](要件定義.md)を参照してください。

GUIが「保存先を利用できません」と表示する場合はWindows資格情報マネージャーを確認してください。
回復できない環境では、`HF_TOKEN`環境変数、またはインストール先（開発版ではrepository直下）の
`.env`に`HF_TOKEN=...`を設定できます。GUIとprofile CLIは同じOSユーザーの資格情報
（service `utteran`、user `huggingface`）を参照します。

GUI設定はOS user config directoryの`settings.json`へ原子的に保存します。theme、language、既定profile、
既定入出力directoryだけを保持し、選択した入力file、文字起こし本文、検索語は保存しません。
話者表示名を保存する場合はGUI設定でなく文字起こしと同じジョブdirectoryの`presentation.json`へ置き、
ジョブ削除時に一緒に削除します。API応答は`Cache-Control: no-store`でbrowser cacheを抑止します。

## 開発と品質保証

```console
uv sync --extra dev --extra gui
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run mypy
uv run pytest -m "not requires_model"
uv lock --check
```

CIはLinux/Windowsのモデル不要testと静的検査を行います。native build、実model、GPU、長時間、性能は
CI対象外です。release品質は[統合受入試験ハーネス](tools/acceptance/README.md)で確認します。

公開履歴の再監査:

```console
uv run python tools/public_history_scan.py --json output/public-history-scan.json
uv run python tools/public_history_scan.py --worktree --fail-on-findings
```

利用者固有の文字列は公開CIに置けないため、ローカルで照合します。`build`は`input/`の
**file名だけ**とWindows user名からSHA-256 patternを作り、ファイル内容は開きません。
patternと結果はGit対象外の`output/`へ置き、値は画面や報告JSONに出力されません。

```console
uv run python tools/private_history_match.py build \
  --input-dir input --output output/private-patterns.json
uv run python tools/private_history_match.py scan \
  --patterns output/private-patterns.json --json output/private-match.json
```

CIはpatternを必要としない汎用のemail形式、user絶対path、media拡張子検査だけを
現在treeに対して行います。固有値照合はpatternを持つ利用者がローカルで実行してください。

## Documentation

- [要件定義・設計](要件定義.md)
- [Release手順](docs/リリース手順.md)
- [Phase 3d統合受入結果](docs/受入試験統合結果_Phase3d.md)
- [Phase 4a公開履歴監査](docs/公開履歴監査_Phase4a.md)
- [Phase 4a照合走査](docs/照合走査_Phase4a.md)
- [Phase 5a GUI手動確認](docs/Phase5a_GUI_手動確認手順書.md)
- [Phase 5b GUI手動確認](docs/Phase5b_GUI_手動確認手順書.md)
- [Phase 5c セットアップウィザード手動確認](docs/Phase5c_GUI_セットアップウィザード_手動確認手順書.md)
- [Phase 5d事前準備](docs/Phase5d事前準備.md)
- [Phase 5d インストーラー手動確認](docs/Phase5d_インストーラー_手動確認手順書.md)
- [変更履歴](変更履歴.md)

## ライセンスとモデル利用条件

utteranのcodeは[MIT License](LICENSE)です。依存library、driver、tool、modelには別のlicense・利用条件が
適用されます。特にgated modelはdownload前の同意が必要です。gatedでないmodelも、利用・再配布前に
model cardと配布元の最新条件を利用者自身で確認し、必要な同意・表示を行ってください。

詳細は[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。この文書は法的助言では
ありません。
