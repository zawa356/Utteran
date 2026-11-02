# B3機密情報・実行境界

## 秘密値と診断

GUI→workerのargvはprofile Python・固定module・protocol版だけ。要求JSONはstdinだけへ送る。
raw request/response、Pydantic errors()/json()、traceback、コマンド全文、生stderrをログへ保存しない。
DTOのrepr/strは値を隠し、公開projectionは固定event型・stage/state/fraction/sequence/codeに限定する。
Prompt/用語集/入力path/文字起こし/任意field名を公開projectionへ含めない。

worker mainはprotocol用にstdoutのfdを複製し、元のstdout/stderrをdevnullへ向ける。
backendのprint/native fd書込みがJSONLを汚染したり、private値を通常診断へ出したりしない。
ConfigはBaseSettingsのため、model_validateでも省略値にenvを適用したり、不要なenv JSONの解析で
失敗したりする。worker mainはgeneral/asr/diarization/output/ffmpeg/alignmentに対応する
UTTERAN_設定sectionの環境変数を大小文字を区別せず除去し、明示requestとcore既定値だけで変換する。
profile/token/SDKのruntime変数は維持する。この処理はworker専用process内に限定し、旧CLIの優先順位は変えない。
Python loggingもworker process内で無効化し、runtime logging/raw diagnostic保存を開始しない。
protected診断を後から安全に公開する仕組みはまだなく、B3では固定codeを正本とする。
この境界は`python -m utteran.worker`のmainにある。内部serve/executeを同じGUI processへimportして
実行する利用法はサポートしない。

completedのpathとhashは親が照合するprivate metadata。通常progressや公開callbackへは出さない。
request・診断の自動一時保存はない。一方、既存coreのjob manifest/intermediate/result/exportには
入力path・Prompt依存hash・文字起こし・失敗時のprivate errorが残り得る。これはcoreのprivate成果物であり、
通常ログへ転送しない。新snapshotのACL/暗号化/保管ポリシーをB3で保証するものではない。

## token規約の監査

既存CLIはenv → .env → OS keyring、GUIはsession tokenを子のHF_TOKENへ渡す方式。
その経路を変更しない。IPCにtoken fieldはない。workerは明示EnvironmentTokenProviderを使い、
Config.load/defaultのdotenv再読込をせず、tokenをeventやConfigのJSONへ格納しない。
B3の許可構成は話者分離無効・ローカルFWだけなのでHF認証を必要としない。
HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE/telemetry無効もSDK import前に設定する。
これはネットワークsandboxではなく、以下の許可経路監査を補強する設定である。

## モデル初期化の監査・許可範囲

lockのfaster-whisper 1.2.1では、WhisperModelにdirectoryを渡すとdownload_modelを通らないが、
tokenizer.json欠落時はtokenizers.Tokenizer.from_pretrainedへ進む。そのためmodel.bin/config.json/
tokenizer.json/preprocessor_config.jsonの存在を事前に必須とする。ローカルdirectoryを直接渡し、
Hub IDやmodel catalogueの暗黙解決を許さない。tokenizerはfrom_file、VADは同梱silero_vad_v6.onnx、
CTranslate2のモデルロードはローカルファイル。モデル破損/同梱runtime欠落は取得せず失敗する。
監査はこのlockのライブラリに対するもので、未知の改造SDKや別versionに対する保証ではない。

pyannoteはPipeline.from_pretrainedによるtransitive component解決が残る。ローカルsnapshotの
存在だけでは全取得禁止を保証できないため、B3ではenabled diarizationをunsupported_settingで拒否。
whisper.cppはnative/VAD/OpenVINO資源とbackend初期化の検証が別途必要で、B3では未対応。
GenAI、用語集、実験引数はB2 adapterの拒否も維持する。CLIへ迂回しない。
workerはvenv構築、uv sync、native build、モデル取得/削除、profile再構築を実行しない。

## native argv・プロセス

既存whisper.cppの--promptにはOS argv露出がある。入力path等をnative argvへ渡す処理もある。
GUI→workerのstdin化だけで全子processのargv保護が完了するとは扱わない。
許可構成でもffmpegは既存coreどおりinput/output pathをargvに持つ。全native argvのpath秘匿は未解決。

起動は登録profileの絶対Python path、-I、固定module、shell=False。
ユーザー要求からexecutable/module/native/venv/ffmpeg差替えを受けない。
Windowsは既存CREATE_NO_WINDOW/Job Object、Linuxは新session・process groupを使う。
親の通常終了/通信異常時はtree cleanupし、leader退出後のPOSIX子孫もgroupへkillを送る。
WindowsはJob Objectを閉じる。Linux親の強制終了時はstdin EOFから協調cancelへ進むが、
応答しないnative区間の即時退出は未保証。インフラkill後のlock/成果物は自動的に整理済みと報告しない。
EOFや非zero終了をユーザーcancelに分類せず、再開はcore既存の検査に委ねる。
EOF/pipe切断時にも秘密値を一時ファイルへ保存しない。

## PR #24受理前拒否の追補

IDなしerrorはhello直後・accepted前・全ID null・入力/フレーミング系5codeだけを許す。
実行成功/失敗やcancelledをIDなしで申告できない。accepted後はerrorにも全ID一致を要求する。
部分nullはDTOで、ID詐称/順序/重複terminalはEventSequenceで拒否し、private値はprojectionへ出さない。
詳細と実worker結合試験は[検証追補](pr24-review-validation.md)を参照。
