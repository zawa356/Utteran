# utteran

[English](README.en.md) | 日本語

utteranは、音声・動画から話者別の文字起こしをローカル生成するデスクトップアプリ／CLIです。
会議・インタビュー・講演を、SRT / VTT / JSON / TXT / Markdownへ出力します。
入力音声をクラウド文字起こしAPIへ送信しません。

> 現在の開発版: `0.1.28`（未release）。API・設定は1.0まで変更されます。

## 主な機能

- faster-whisper: CPU / NVIDIA CUDA
- whisper.cpp v1.9.2 + token JSON timeline patch: CPU / OpenVINO / Vulkan / OpenVINO+Vulkan
- pyannote.audio 4.x話者分離: CPU / NVIDIA CUDA / Intel XPU
- 単一ファイル／folder batch、段階別resume、5形式出力
- profile別venv、model／job／native build管理、device診断
- WindowsデスクトップGUI（進捗・中断、結果閲覧・検索、ジョブ履歴、再出力）
- Windows番号menu (`start.ps1`) と自動化向けCLI
- Windowsインストーラー、ポータブルZIP、Python直起動

主対象はWindows 10/11、Python 3.11/3.12です。Linuxは副対象で、CIがモデル不要testとimportを
確認します。GPU、native build、実model、長時間処理は対象hardware上の受入試験で保証します。

## 起動経路を選ぶ（Windows）

| 起動経路 | 向いている用途 | データ配置 |
|---|---|---|
| `utteran-setup-<version>.exe` | 継続利用、通常はこちらを推奨 | Windowsのユーザー領域（従来どおり） |
| `utteran-portable-<version>.zip` | 一時利用、PCへutteranのデータを残したくない場合 | 展開先の`data`配下 |
| `launch-python.bat` | Smart App Control環境、未署名exeを使えない環境、既存Python環境から使う場合 | venvはrepository配下、その他はWindowsのユーザー領域 |

`build.ps1`で生成する配布対象は`dist/release`にまとまります。installer、portable ZIP、各
`.sha256`だけをここから配布し、`dist/staging`は配布しない中間生成物です。

ポータブル版はZIPを展開し、直下の`utteran-gui.exe`を起動します。設定、ログ、ジョブ、モデル、
キャッシュ、ffmpeg、native build、プロファイルvenvは展開先だけに作られるため、必要な出力を
別の場所へ移してから展開フォルダーを削除すればutteranの管理データは残りません。

ただし、ポータブル版ではHugging Face tokenを保存せず、起動のたびに入力が必要です。
venvとモデルの取得後は数GBを使用し、展開フォルダーを移動するとvenvの再構築案内が出る場合が
あります。USBメモリでは構築・モデル読込・処理が遅くなる可能性があります。ポータブル版も同じ
`utteran-gui.exe`を使うため、Smart App Controlを回避するものではありません。

Python直起動はrepositoryを取得し、Python 3.11/3.12、uv、ffmpegを利用者自身で導入してから
`launch-python.bat`を実行します。batはその起動に限ってPowerShellを`ExecutionPolicy Bypass`で呼び、
恒久的な実行ポリシーは変更しません。不足項目と導入先、失敗理由、次の操作、診断ログの保存先を
consoleに表示します。前提条件を自動導入せず、GUIと推論profileをuv製venvの`python.exe -m`で
起動します。

この経路がSmart App Controlを回避できるかは未検証です。Python自体が起動しても、構成によっては
torch、OpenVINO、OpenVINO GenAIや、利用者環境で構築したwhisper.cppのnative DLLが阻止される
可能性があります。一部構成だけ動作する可能性も含め、実機確認後に記述を更新します。

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
5. 初回起動時はセットアップウィザードで、profile、話者分離、Hugging Face token、モデルを
   先に選びます。確認画面で所要時間（初回はおおむね10～45分）とダウンロード量を確認して
   開始した後は、環境構築・token preflight・モデル取得・実動作確認が無人で進みます。
   **この時点でインターネット接続と数GB程度の空きディスク容量が必要です。**途中でGUIを
   閉じても、完了済み段階を保持して次回起動時に続きから再開します。
   保存状態にprofileがない場合は実行を開始せず、CPU/GPUを自動検出する構成選択画面へ戻ります。

GUIはMicrosoft Edge WebView2ランタイムを使用します。Windows 11には標準搭載されています。
Windows 10で起動時に画面が表示されない場合は、
[WebView2ランタイム](https://developer.microsoft.com/microsoft-edge/webview2/)を導入してください。

アンインストールは「アプリと機能」から行えます。アプリ本体、プロファイル環境（`.venvs`）、
ダウンロード済みモデル、OpenVINO／デバイスキャッシュ、native build、ffmpegは再構築可能なため
常に削除されます。ジョブ履歴と文字起こし結果、GUI／CLI設定、メモリ較正データ、ログ／診断／
ベンチマーク、Windows資格情報マネージャーのHugging Face tokenは利用者データとして既定で保持し、
対話アンインストール時だけ項目ごとに削除を選べます。無人アンインストールでは利用者データとtokenを
保持します。利用者が指定した出力フォルダーは常に対象外です。`uv`も他の用途と共有される可能性が
あるため削除しません。

0.1.22～0.1.26のアンインストーラーには実行時エラーがあり、アンインストールを完了できません。
該当版を使用している場合は、0.1.27以降の修正版を同じ場所へ上書きインストールしてから、改めて
「アプリと機能」からアンインストールしてください。上書きによりアンインストーラーが修正版へ
差し替わります。手作業でレジストリを削除する必要はありません。

新しい版は同じインストーラーを実行して上書きできます。utteranが実行中なら、インストーラーが
更新前に終了を案内し、GUIが起動した子プロセスも含めて停止します。既存profileの依存lockが新しい版と
一致しない場合はGUIに再構築案内が出るため、セットアップから同じprofileを再構築してください。
古い版へのダウングレードは設定・ジョブの互換性を失う可能性があるため警告し、無人実行では拒否します。

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

各releaseのGitHub Releasesページには、インストーラーとポータブルZIPのSHA-256ハッシュを掲載しています。
PowerShellでダウンロードしたファイルのハッシュを計算し、一致することを確認してください。

```powershell
Get-FileHash .\utteran-setup-<version>.exe -Algorithm SHA256
Get-FileHash .\utteran-portable-<version>.zip -Algorithm SHA256
```

表示された値がReleasesページ記載の値と一致しない場合は、ファイルを削除し、公式の
[GitHub Releases](https://github.com/zawa356/Utteran/releases)から再ダウンロードしてください。

## Pythonから直接使う（Windows）

一般利用者向けの入口は次のbatです。前提条件の確認後、GUI環境がなければ構築し、Python
インタプリタからGUIを起動します。構築中は進行中として表示し、失敗扱いにはしません。

```bat
launch-python.bat
```

確認だけを行いGUIを開かない場合は`launch-python.bat -CheckOnly`を使えます。診断ログは
`%LocalAppData%\utteran\utteran\Logs\python-launch-*.log`です。

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
案内します。入力は長時間処理より前にすべて完了し、確認後は操作なしで最後まで進みます。

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

「モデル管理」では推奨／全カタログ、導入状態、用途、概算／実サイズ、保存先を確認し、取得・削除・
検証とOpenVINO encoder IRの生成・削除を行えます。取得中は取得量、割合、転送速度、残り時間を表示し、
完了時に一覧を自動更新します。取得やIR生成は確認後にだけ始まり、進捗表示中にキャンセルできます。
文字起こし、モデル取得、IR生成は共通キューへ入り、GPUメモリ競合を避けるため1件ずつ実行されます。
キューはGUI終了時に破棄され、稼働中の子プロセスも終了します。キャンセル後は終了処理中の表示を維持し、
OpenVINO GenAIでは15分音声でGPU約1分、NPU約3分、CPU約5分、長い音声ではさらに応答に時間が
かかる場合があります。次回起動時はCLIのレジューム／再取得を使用します。IR生成では追加のPyTorch重み
（最大約3GB）が必要です。whisper.cppモデルを選んだ場合、ウィザードはスモークテストより前にnative
buildの欠落を検出し、前提が揃っていれば確認なしで再構築します。画面は数分～数十分の目安、経過時間、
継続中であることを表示します。失敗時は不足した前提とnative build logの場所を表示します。

入力file、入力folder、出力folderは選択dialogから指定でき、pathの手入力も利用できます。入力欄へ
Explorerから1件のfileまたはfolderをdrag-and-dropすると、pywebviewのnative連携で絶対pathを取得します。
複数項目と非対応形式は理由を表示して受理しません。folder内の複数fileは「サブフォルダも処理」と
glob指定でbatch処理します。使用した入力directoryと出力directoryだけを次回の既定値として記憶し、
入力file名や履歴は保存しません。

エラー、ジョブ／モデル／OpenVINO IRの削除、取得・生成の確認はアプリ内modalで表示します。Enterで確定、
Escまたは背景clickで取り消せます。破壊的な操作では「キャンセル」に初期focusを置きます。file／folder
選択は従来どおりOSのnative dialogです。

テーマは既定でWindowsのライト／ダーク設定に追従し、設定画面からライトまたはダークへ固定
できます。既に明示保存されているテーマはそのまま尊重されます。

話者分離にはpyannote modelの利用条件への同意とHugging Face tokenが必要です。取得前に必ず
[ライセンスとモデル利用条件](#ライセンスとモデル利用条件)を確認してください。ウィザード内で
アカウント作成、モデル利用条件への同意、read token発行の各ページを開き、そのままマスク入力欄へ
保存できます。**利用条件への同意とtoken発行は別の手続きです。**保存後はOSキーリングから実際に
再取得できることを確認し、値自体は再表示しません。話者分離を使わずにセットアップすることもでき、
その場合は話者ラベルが出力されません。後から有効にするには、GUIの「設定」→「セットアップ
ウィザードを開く」で再開し、「話者分離を使う」を選んでください。
対象モデルが既に完全な状態でローカルへインストール済みなら、外部認証の一時的な失敗で
セットアップを差し止めず、そのローカルモデルを利用します。

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
| `cuda` | NVIDIA GPU | ○（CUDA） | ○（CUDA） | CUDA 12.6 PyTorch、faster-whisper、pyannote、Sudachi |
| `intel` | Intel GPU（Arc／内蔵GPU） | ○（OpenVINO／Vulkan／GenAI） | ○（XPU） | XPU PyTorch、OpenVINO GenAI、whisper.cpp、Sudachi |
| `vulkan` | AMD等のGPU、またはNVIDIA/Intel以外 | ○（Vulkan） | ×（CPUで実行） | CPU PyTorch、Vulkan whisper.cpp、Sudachi |
| `cpu` | GPUなし | ×（CPUで実行） | ×（CPUで実行） | CPU PyTorch、faster-whisper、pyannote、Sudachi |
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

Intel profile相当は`--extra xpu --extra whisper-cpp --extra openvino --extra openvino-genai --extra japanese`、Vulkan
profile相当は`--extra cpu --extra whisper-cpp --extra japanese`です。`cpu`/`cuda`/`xpu` extrasは
同時指定できません。既存の推論profileは0.1.16更新後に同じ`setup.ps1 -Profile <name>`を再実行して
Sudachi依存を同期してください。追加容量は実測約210.7 MiBで、GUI専用venv／installerには辞書を
含めません。0.1.16 installerは19,833,496 bytes（約18.9 MiB、0.1.15比+12,677 bytes）です。

## Modelとtoken

モデルのGPU可否はファイル名の`q5`等ではなく、バックエンドと検出済みデバイスで決まります。
`faster-whisper`（CTranslate2）はCPU profileではCPU実行、CUDA profileではCUDAを利用できます。
`whisper-cpp`（GGML）は同じモデルのf16／q5／q8すべてで、ビルド済みVulkan／OpenVINO構成を利用
できます。量子化は一般にファイルを小さくし速度を上げる代わりに精度へ影響し得る調整であり、GPUを
有効にするスイッチではありません。`tiny`／`base`／`small`は試用・低スペック環境、`medium`は
large系より軽い精度重視、`large-v3-turbo`は速度と精度の既定バランスです。Kotoba-Whisperは
日本語特化モデルです。

OpenVINO GenAIは、cmake／MSVC Build Tools／Vulkan SDKを必要としないIntel CPU/GPU/NPU経路です。
モデルは`openvino-genai:large-v3-turbo-int8`を明示取得してください。英語では話者分離を含めて
利用できますが、日本語・中国語などでは単語時刻が発話単位へ融合するため、話者分離と併用できません。
言語`auto`と話者分離の組み合わせも安全のため拒否します。話者分離なしなら日本語でも利用できます。
`auto`からは選ばれず、禁止構成でも別backendへ黙って切り替えません。

```console
uv run utteran models download openvino-genai:large-v3-turbo-int8
uv run utteran transcribe lecture.wav --asr-backend openvino-genai \
  --asr-model large-v3-turbo-int8 --asr-device gpu --no-diarization --language ja
uv run utteran models genai-cache
```

NPUは固定依存版（OpenVINO GenAI 2026.2.1）の本機実測で初回ロード約306秒、
cache 2,212,110,925 bytes（約2.06 GiB）、cache後約4.8秒で、
現時点ではGPUより遅いため非推奨です。
GUIでは「非推奨の構成を表示する」を有効にした場合だけ理由付きで表示します。CLI／benchmarkでは
測定用に常に明示指定できます。cacheはutteranのuser cache配下に置かれ、アンインストール時の
モデル削除対象および`scripts/reset-env.ps1`の削除対象です。

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

Silero VADは既定で有効です。ウィザードが軽量VADモデルを取得し、モデル管理からも取得・検証できます。
旧環境でVADモデルが未取得の場合は突然エラーにせず、その実行だけ警告してVADなしで続行します。
`[asr.whisper_cpp].vad = false`で明示的に無効化できます。既定変更により既存ジョブのASR設定hashが
変わり、ASR以降が再計算されます。

whisper.cppのDTW/token時刻が壊れている場合（単一tokenへ数十秒のsegment全体を割り当てる、または
単語群がsegmentの一部分だけへ潰れて残りが無語のまま消える）、その単語時刻だけを破棄し、
segmentの本文とoffsetは保持したまま話者分離へsegment単位でfallbackします。以前バージョンは
該当segmentを本文ごと除外していましたが、実会議録音で正常な発話まで巻き込んで消えることを
実測で確認したため、本文を残す方式へ変更しました。判定の閾値は
`[asr.whisper_cpp].max_word_duration_seconds = 3.0`で変更でき、fallbackしたsegment数・word数・
秒数は本文なしの警告・構造化eventへ記録します。

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

### 長時間音声の話者分離

whisper.cpp v1.9.2は、VAD圧縮後のtoken時刻を元音声timelineへ戻すgetterを提供します。ただし公式の
`whisper-cli -ojf`はraw token dataをJSONへ出力するため、utteranはfull JSONのoffsetだけを新getter
由来にする小さなsource patchをbuild時に適用し、patch levelをnative manifestへ記録します。
`t_dtw`は圧縮timelineのままなので、Python側はsegment内の有効なoffsetを優先します。これにより
`word_timestamps = "auto"`で話者分離が有効な実行、および`"always"`の実行でも内蔵VADを無効化
せず、無音除去と元timeline上の単語時刻を併用します。v1.9.1の既存native buildは再buildが必要です。

話者割当は、単語ごとの瞬間判定ではなく、話者区間との重なりと切替penaltyを使うViterbi系列
最適化です。連続発話中の短い揺らぎは抑え、十分な無音、長い話者区間、明確な重なりに裏付けられた
切替は通します。従来の文字数／短時間による`A→B→A`吸収は、短い相槌まで消すため廃止しました。
重なりのない単語は原則`UNKNOWN`で、両側が同一話者の短いgapだけを橋渡しします。

日本語（`--language ja`、または`auto`の検出結果が`ja`）では、無音0.02秒以下の話者境界を
SudachiPyの文字位置へ最大4文字だけ補正します。分割単位Aが既定です。英語など日本語以外は
Sudachiを通しません。依存がない環境でも処理を止めず、警告して従来の境界を使います。

3秒を超える異常な単語時刻が混ざったsegmentは、異常な単語だけを除き、正常な単語時刻と本文を
保持します。正常な単語が一つもない場合だけsegment単位へ退避します。単語群がsegmentの片端へ
偏っているだけなら、単語群の観測済み包絡へsegment時刻を直し、単語は捨てません。DTWが全て
`-1`でも、有効なtoken offsetは低信頼として保持します。按分などによる時刻の生成は行いません。

JSONの`speaker_confidence`は、単語時刻が完全なら`high`、単語時刻が一部欠ける、DTWなしのoffset
だけを使う、または単語時刻が全くない場合は`low`です。`low`のsegmentは本文の位置を推測して
話者分割せず、前後とも結合しません。単語時刻が全くない場合の時刻はpyannote検出発話の包絡へ
縮め、検出区間がない場合だけ4文字/秒＋1秒（最小1秒）で上限を設けます。話者名と本文は保持し、
字幕・テキスト表示は従来どおりです。field追加は後方互換なので`schema_version`は1を維持します。

`UNKNOWN`はViterbiの状態として通常の話者と同じ切替penaltyを受けます（`A→UNKNOWN→A`のような
短い出入りも、話者切替と同様に十分な無音や重なりの裏付けがなければ抑制されます）。それでも
残る`UNKNOWN`のうち、継続長が`min_unknown_duration`未満または文字数が`min_unknown_characters`
未満のものだけを、より長い側の既知話者へ吸収します。実会議録音（約24分45秒）では、通常話者と
同じpenaltyへの是正とこの吸収により、発話途中のUNKNOWNは15件から0件になりました。

境界の両端が既知話者だが割り当てられた話者がその区間でほとんど重ならない極小fragment
（`max_unsupported_fragment_duration`秒未満かつ`max_unsupported_fragment_characters`文字以下、
かつ話者区間との重なりが`min_fragment_speaker_overlap`秒未満）も、より長い隣接segmentへ吸収し
ます。無音で区切られた正当な短い相槌（重なりに裏付けられた話者）は対象外です。

同一話者segmentの結合閾値は0.5秒のままで、結合回数・gap分布・各段階のsegment数は
`diarization_statistics`と`alignment_statistics`イベントへ本文なしで記録します。既存ジョブは
ASR/merge policy hashの更新により該当stage以降を自動再計算します。
異常word時刻からsegment単位へfallbackした区間はASR offsetを残すため、前後の同一話者とは
結合しません。

```toml
[alignment]
speaker_switch_penalty = 0.75
silence_switch_threshold = 0.3
min_clear_turn_duration = 0.5
max_same_speaker_bridge_gap = 0.3
unknown_emission_score = 0.35
min_unknown_duration = 1.0
min_unknown_characters = 2
max_unsupported_fragment_duration = 0.5
max_unsupported_fragment_characters = 3
min_fragment_speaker_overlap = 0.05
merge_gap = 0.5
boundary_snap_enabled = true
boundary_snap_unit = "A"                # A | B
boundary_snap_max_characters = 4
boundary_snap_max_gap = 0.1
fallback_characters_per_second = 4.0
fallback_duration_padding = 1.0
fallback_min_duration = 1.0
```

調査用には、ジョブディレクトリの中間JSONから本文を出さず統計だけを表示できます。

```console
uv run python tools/diarization_stats.py <job_dir>
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

GUIは独自のbackend既定を持たず、選択profileの`utteran devices --json`が返すauto構成を既定にします。
Intel/Vulkan環境ではnative buildとGGMLモデルが揃うとwhisper.cppのVulkan等が選ばれます。初回
ウィザードでは必要な構成だけを自動再構築し、faster-whisper／OpenVINO GenAIではnative判定を行いません。
手動構築時はCMakeを`whisper-cpp` extraから導入できます。WindowsではMSVC C++ Build Toolsが全構成に
必要で、Vulkan構成にはVulkan SDK（headers、`vulkan-1.lib`、`glslc`）も必要です。GPUドライバのVulkan
Runtimeだけではビルドできません。構成差は大きく、単一Intel Arc 140T環境の
300秒素材ではfaster-whisper/CPUが74.34秒（実時間比4.04倍）、whisper.cpp/Vulkanが21.62秒
（13.88倍）でした。素材・driver・modelで変動するため保証値ではありません。

```console
uv run utteran native build
uv run utteran models download whisper-cpp:large-v3-turbo-q5_0
uv run utteran models prepare-openvino whisper-cpp:large-v3-turbo-q5_0 --device GPU
uv run utteran transcribe meeting.wav --asr-backend whisper-cpp
```

VulkanとOpenVINO+Vulkanの順位は音声長で変わります。Intel Arc 140Tで、180秒ではVulkan
15.363秒／OpenVINO+Vulkan 19.638秒だった一方、24分46秒では110.730秒／65.141秒と逆転しました。
単一環境・素材の観測であり、他環境へ一般化はできません。

Phase 6a以降のbenchmarkはバックエンド、デバイス構成、モデル、量子化を独立した測定対象として
扱います。`quick`（短尺・速度のみ、10〜20分）、`standard`（中尺・速度＋精度、30〜60分）、
`detailed`（複数長・複数モデル、数時間）の3モードがあります。実行前にPhase 5kのキャッシュ済み
デバイス検出を使い、ハードウェア上不可能な構成は表示せず、モデル取得・native build・OpenVINO IR
生成で利用可能になる構成は「準備すれば可能」と案内します。

```console
uv run utteran benchmark --audio long-sample.wav \
  --durations 180,900,full --variants vulkan,openvino_vulkan \
  --json benchmark.json
uv run utteran benchmark --audio tests/fixtures/benchmark/japanese_reference.wav \
  --mode standard --json benchmark.json --markdown benchmark.md
uv run utteran benchmark --audio sample.wav \
  --targets whisper-cpp/vulkan/large-v3-turbo,faster-whisper/cpu/large-v3-turbo
uv run utteran benchmark --audio sample.wav \
  --targets openvino-genai/gpu/large-v3-turbo-int8
uv run utteran benchmark --audio long-sample.wav --apply
```

速度スコアは`実時間比 × 100`（780なら7.8倍速）で、large-v3-turboだけが基準値です。他モデルは
参考値として表示します。日本語精度はNFKC正規化後に空白・句読点を除いたCERを使い、
`精度スコア = max(0, 1 - CER) × 100`とします。WAVと同名の`.txt`または`--reference-text`が正解です。
表示にはモデル、量子化、音声長、単語timestamp条件と「1時間の音声→約N分」を併記します。

**このスコアは目安です。音声の内容、長さ、同時に動作する他の処理により変動します。
スコアが2倍でも、処理時間が半分になることを保証するものではありません。**

結果はログ保存先の`benchmarks/`へschema v3 JSONとして逐次保存され、Ctrl+Cでも完了分を残します。
環境情報はCPU/GPU/driver再検出用fingerprint、runtime/model/versionを含みますが、認識本文、job、
個人名は含みません。前回結果を自動比較し、utteran更新後は再測定を促します。`--markdown`は共有用、
`--apply`は推奨構成と根拠（モデル、音声長、日時）をconfigへ記録します。既存`--variants`は互換alias
として維持しています。`--variants`と`--targets`は同じdevice解決を使い、`auto`だけが検出結果へ
解決されます。明示deviceは利用不能でも別deviceへ切り替えずerrorになります。JSONの
`target.device_resolution`には要求値、実際のdevice、明示／auto／matrix列挙の別、決定根拠を保存します。
OpenVINO構成はencoder IR生成が必要です。

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
uv run utteran logs path
uv run utteran logs clean
```

一般設定の優先順位は`CLI 引数 > 環境変数 > .env > config.toml > 既定値`です。
トークンの参照元は環境変数、`.env`、OS キーリングの3段階で、この順に優先します。全設定、終了code、JSON schema、resume hashは
[要件定義](要件定義.md)を参照してください。

### デバイス検出について

`utteran devices`（GUIでは起動時に自動実行）は、CTranslate2 / PyTorch CUDA・XPU /
OpenVINO / ONNX Runtime / Vulkan を1つずつ**別プロセス**で検出します。これは、
NVIDIA GPUを持たない環境でCTranslate2/PyTorchのCUDA検出がネイティブ層で無限に
ハングすることがある（Ctrl+Cでも復帰しない）ためです。各プローブは既定20秒で
タイムアウトし、確実にプロセスごと終了させて次のプローブへ進みます。タイムアウトは
「利用可能」ではなく「判定不能」として扱われ、誤って高速な構成が選ばれることは
ありません。

- NVIDIA GPUがない環境では、PyTorch CUDA/XPUプローブがタイムアウトするため
  **初回（未キャッシュ）の検出に数十秒程度かかる**ことがあります。GUIはこの間、
  起動処理やジョブキューをブロックしません。
- 全プローブが正常に完了した結果だけが、ハードウェア・ドライバ・パッケージ構成をキーに
  キャッシュされ、2回目以降は数秒で完了します。失敗・タイムアウトはキャッシュされません。
  CLIでは`utteran devices --refresh`、GUIでは「再検出」でキャッシュを破棄して再試行できます。
- タイムアウト秒数は`utteran devices --probe-timeout <秒>`、または
  `config.toml`の`[general] device_probe_timeout_seconds`で変更できます。

詳細な設計（分離・タイムアウト・キャッシュの根拠）は[要件定義](要件定義.md)4.5章を
参照してください。

### faster-whisper CPU推論とPyTorchの関係について（Phase 5m）

CTranslate2（faster-whisperの実行基盤）は、実際には使わないモデル変換ヘルパーの
ためだけに`import ctranslate2`時点で無条件に`torch`をインポートします。この
プロジェクトのIntel profileが導入するPyTorch（XPUビルド）は800 MiB超の
ネイティブDLL（`torch_xpu.dll`）を含み、特定のIntel iGPU/ドライバ構成では
このDLLの初期化だけで実CPU時間を大量に消費し、完了しないことがあります
（実測: 1000秒超のCPU時間を消費しても完了せず）。**これはCUDA/XPUデバイスを
明示的に問い合わせているわけではなく、`ctranslate2`をインポートするだけで
発生します。** faster-whisperのCPU/CUDA推論はCTranslate2のネイティブ実行に
torchを必要としないため、`utteran.devices.suppress_torch_import()`で
`ctranslate2`インポート中だけ軽量な代替モジュールに置き換え、直後に元へ戻す
ことでこの問題を回避しています（話者分離が後続で本物のtorchを必要とする場合は
通常どおり実importされます）。詳細は[要件定義](要件定義.md)4.5.4章を
参照してください。

## ログ

既定の保存先はインストール先（ソース実行ではリポジトリ直下）の`logs/`です。書き込みできない
場合はOSのユーザーログ領域へ自動退避し、`utteran logs path`とGUI設定画面で実効パスを確認
できます。`app.log`はローテーションされ、CLIごとの構造化イベントは`cli/*.jsonl`、デバイス診断は
`diagnostics/`へ保存されます。ジョブ内の従来の`utteran.log`も互換性のため維持します。既定保持は
30日、通常ログ100 MiB、rawログ1 GiBで、日数と容量の両方を起動時に適用します。

構造化イベントは実行構成、OpenVINO IRロード成否、フォールバック、device、stage所要時間、実時間比、
モデル取得／IR生成、エラー分類だけを記録し、文字起こし本文を含みません。保存先と保持量は
`[general]`の`log_dir`、`log_retention_days`、`log_max_mib`、`raw_log_max_mib`、または対応する
`UTTERAN_GENERAL__...`環境変数で変更できます。

`raw_subprocess_logs = true`（環境変数は`UTTERAN_GENERAL__RAW_SUBPROCESS_LOGS=true`）を明示すると、
秘密値をマスクしたサブプロセスstderrを`raw/<job_id>/`へ保存します。**ここには文字起こし本文が含まれる
可能性があります。既定はfalseです。** 有効時は起動時に警告し、GUI設定画面にも常時警告を表示します。
不要になったら無効へ戻し、`utteran logs clean`で削除してください。

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

正解時刻付きの話者分離結果は、モデル不要の評価コマンドで比較できます。話者番号は一致して
いなくても時間重なりが最大になる対応へ自動的に揃え、DER、話者境界誤差、語中の話者境界、
0.5秒未満／1語だけの話者島、UNKNOWN時間率、短い相槌の保持率を本文なしで出力します。

```powershell
utteran eval reference.json hypothesis.json --output metrics.json `
  --max-der 0.2 --max-mid-word-boundaries 0 --max-unknown-ratio 0.2
```

再現可能な3話者合成fixtureと正解timelineは
`tests/fixtures/diarization_quality/`にあり、`Q1-DIARIZATION-REFERENCE`がモデル不要の
受入ケースとして品質指標を検査します。

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
