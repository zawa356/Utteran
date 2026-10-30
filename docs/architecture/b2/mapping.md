# ProcessingSettings → legacy Config対応

## 入口・決定性

LegacyはConfig.loadの`CLI > env > .env > TOML > defaults`を維持する。Structuredは`adapt_settings(settings, context)`だけを使い、**Config.load、環境設定source、filesystem、GPU probe、model downloadを呼ばない**。Config.model_validateと既存ASRConfigのstatic catalog alias正規化を再利用する。変換成功は実model/driverが利用可能という意味ではない。

ExecutionContextにはbase_config（全legacy設定）、明示したasr_backend/asr_model/asr_device/diarization_device/compute_type、whisper.cpp時のnative_variantを渡す。backend/model/device/computeが空/autoのcontextは拒否。DTOのbackend/modelがautoの場合もcontextをそのまま使い、自動選択をしない。DTOに具体値があればcontextと一致を検査する（model aliasはcore canonical IDで比較）。

## 対応表

| DTO/Context | core | 規則 |
|---|---|---|
| asr.backend/model | ASRConfig.backend/model | context明示値、既存catalogのbackend-qualified aliasを正規化。未知custom ID/pathの許可はlegacyと同じで、実在検査はruntime |
| context.asr_device/compute_type | ASRConfig.device/compute_type | 明示値のみ。環境をprobeして埋めない |
| language | ASRConfig.language | trim、case-insensitive auto→None。CLIの既存規則と同じ |
| initial_prompt | ASRConfig.initial_prompt | None/空文字/通常文字列をそのまま保持。省略はB1のrequired-fieldエラー |
| glossary / extra_arguments | 未対応 | 非空glossary、存在するextra_argumentsを明示拒否。compile/実行しない |
| beam_size / condition_on_previous_text | ASRConfig同名 | faster-whisperは両方。whisper.cppのcondition_on_previous_text=Trueは現wrapperで効かないため拒否 |
| word_timestamps | ASRConfig.word_timestamps | 旧値auto/always/never保持。faster-whisperのneverは旧wrapperが常にTrueで無視するためstructuredでは拒否 |
| vad.enabled | asr.vad_filter、whisper.cpp時はwhisper_cpp.vadも | 両者へ同じ値。namespaceのvadと矛盾したら拒否 |
| vad.threshold | whisper_cpp.vad_threshold | whisper.cppのみ。共通thresholdとnative thresholdの矛盾拒否。faster-whisperは現wrapperに対応経路がないため指定を拒否 |
| faster_whisper.compute_type | asr.compute_type | 省略/autoはcontextを使用、具体値はcontext一致必須 |
| WhisperCppSettings | WhisperCppConfig同名field | 指定した全fieldを適用、未指定はbase_configを保持。temperature/threads/dtw/no_context/VAD/閾値/repetition/word duration/pathを削減しない |
| context.native_variant | asr.whisper_cpp.variant | whisper.cppで必須、deviceと一致。既存4variantのみ。OpenVINO+CUDA等の矛盾は拒否 |
| inactive backend namespace | 未対応 | 空objectも含め明示拒否。別backendへ勝手に適用しない |
| DiarizationSettings | DiarizationConfig同名 | enabled/backend/model/memory guard等を適用。optional memory_safety_margin省略はcontext保持 |
| device_preference | diarization.device | auto→context。明示値はcontext一致必須。有効なdiarizationでcontextのauto/空を拒否。無効ならautoの保持可 |
| num/min/max_speakers | 同名 | null→0。範囲・排他はB1とcoreの既存validatorで検証 |
| PostprocessingSettings | AlignmentConfig同名 | 全指定fieldを適用。省略optionalはcontext保持。B1の0許容に対しcoreが正数を要求するfieldはtypedエラー |
| OutputSettings | OutputConfig同名 | formatsの順序、labels、BOM、LF/CRLF、show_speakerをそのまま保持 |
| context.base_config.general | GeneralConfig全field | output/job/log/venv/native/default_profile/probe timeout等を保持。input pathはservice引数 |
| context.base_config.ffmpeg | FfmpegConfig全field | pathを保持。変換時にexists/findをしない |
| DTO外のlegacy設定 | base_configの残存field | コピーして保持、黙って削除しない |

## 未対応と既存CLIの区別

structured backendはfaster-whisper / whisper-cppを対象とする。**OpenVINO GenAIは明示拒否**。現wrapperはGenAIへbeam/VAD/condition等の共通必須設定を忠実に渡せず、GenAISettings.max_new_tokensの経路もないため、DTO受理だけで設定を無視して実行しない。既存CLIはadapterを通さず、従来GenAIやautoをそのまま利用できる。B1 DTOを削除・縮小してはいない。

word_timestamps=auto、dtw=auto、memory_guard=autoは既存coreの処理policyであり、デバイス候補の未解決autoとは分ける。前者は値を保持し既存coreに委ね、後者は明示contextを要求する。ソフトウェアの対応候補やハードウェアマトリクスをB2に新設していない。runtimeのdevice validation/fallbackは既存backendの責務であり、contextだけで成功を保証しない。

## 正規化とhash

Phase Aで提案した「空Prompt→null」正規化はB2では適用しない。旧ASRConfigは既定空文字で、既存stage hashは空文字とnullを区別する。両方を変換してhashを変える回帰を避け、**legacy/structured双方でcore上の表現を保持する**。いずれもbackendではPromptなしとして扱うが、表現変更時のASR invalidationは旧規則のまま。用語集やPrompt品質・whisper.cpp no_contextの変更は行わない。

sourcesは各core fieldについてcontext/dto/normalizedを返す。値・Prompt・label・pathそのものは含まず、同入力で同じConfig/source traceになる。Context既定値の適用根拠をcallerが管理でき、変換中に隠れたhost既定値を追加しない。生Pydantic ValidationErrorのinputを表示せずConfigAdaptationError(code, static path)へ変換する。既存core意味検証（validate_asr_configuration）とruntime例外は維持する。
