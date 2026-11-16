# Worker IPC protocol 1

## 起動・フレーミング

```text
<absolute profile python> -I -m utteran.worker --protocol 1
```

`-I`でcwd/PYTHONPATHによるmodule shadowingを避ける。POSIXのPython symlinkは
最終targetへresolveせず、venv内のpathで起動する。shell/PowerShellを使用しない。
設定・入力path・Prompt・tokenはargvに含まない。

stdin/stdoutはUTF-8 JSONL。LFを含めてrequest/controlは16MiB、eventは1MiB以下。
1行1object。本文の改行はJSON escape。bounded readline(limit+1)で先にサイズを検査する。
不正UTF-8/JSON、非object、重複object key、NaN/Infinity、未知fieldを拒否。
受信bufferは最大32 eventのqueueと各record上限で拘束する。stderrは8192byte単位で同時に
読み捨て、保持するmodule欠落検知windowも512byte以下。raw内容を診断へ転送しない。

worker側のstdio（`utteran.worker.main`）: protocol出力はstdoutの私有dup fdだけが持ち、fd 1/2はNULへ。
protocol入力も同様に**stdinの私有dup fd**（非継承）だけが読み、プロセスの標準入力（fd 0、Windowsでは
`STD_INPUT_HANDLE`）はcore/backendのimport前にNULへ差し替える。PR #48のWindows再受入で、
control threadがstdinパイプを同期read中だと、main threadのnumpy拡張moduleのネイティブ初期化
（`numpy/_core/multiarray.py`の`_multiarray_umath`生成）が戻らず、readが完了するまで停止した
（windows-latestで再現。実worker `-I -m utteran.worker`でも`progress asr`後に停止）。
標準入力をNULにした構成と「thread開始前のnumpy事前import」の両方で解消を確認し、前者を採用した
（numpyに限らずctranslate2/torch等の後続ネイティブ初期化にも効き、hello・cancel応答を遅らせない）。
OS内部のどの層が待つのか（CRT/ローダー/パイプI/O）は特定していない。私有入力は`close()`しない
（daemonのcontrol threadがreadline中にbuffer lockを保持するため。終了は従来どおり`os._exit`）。
wire形式・event順序・timeoutは変更なし。

全wire envelopeで`schema_version:1`と`protocol_version:1`を必須とする。
Python構築時は既定1を提供するが、framingは欠落・bool・文字列も拒否する。
JSON Schemaでもwire版をrequiredにしている。B1 ProcessingSettingsのschema版も検証する。

## DTOと要求

[worker.schema.json](worker.schema.json)がオフライン生成物。

- `WorkerRequest`: type=execute、request/job/attempt ID、profile、input_path、B1 settings、
  WorkerExecutionContext、mode=single、resume、force。
- `WorkerExecutionContext`: base_config、asr_backend/model/model_dir/device、diarization_device、
  compute_type、optional native_variant。core dataclassとは別のtransport型。
  - `asr_model`は**解決済みカタログモデルID**（B7 `ResolvedSpec.model_id`）だけ。presetの
    `settings.asr.model`との比較対象であり、pathを入れない。
  - `asr_model_dir`は**同じsnapshotでB7が確定した絶対ローカルモデルディレクトリ**
    （`ResolvedSpec.worker_model_dir`）。**必須**（1～32768字）。欠落・空はschemaで拒否し、
    IDからの自動選択・catalog/cache探索・downloadで補わない（PR #48 P1、下記「モデルID/ディレクトリ」）。
- `WorkerCancel`: type=cancel、同じ3 ID。1 workerへ2回目のexecuteを送れない。
- `WorkerHello`, `WorkerAccepted`, `WorkerProgress`, `WorkerCompleted`, `WorkerError`,
  `WorkerCancelAcknowledged`: discriminated event union。
- `WorkerArtifact`: private path、byte size、SHA-256。入力・本文を通常eventへ載せない。

IDはASCII英数字で始まる最大128字の英数字/underscore/hyphen。pathはIDとして使わない。
profileはcpu/cuda/intel/vulkanの登録enum。base_configの全nested keyはcoreモデルのfield一覧で
検査し、extra=ignoreに任せない。ffmpeg executable、native/venv差替え、raw log指定は拒否。
input/output/job directoryは絶対pathを要求する。Config.loadを呼ばない。

### モデルID/ディレクトリ（PR #48 P1で分離）

以前はB7が`asr_model`へ絶対ディレクトリを入れ、B3がそれをpresetのカタログIDと比較したため、
`settings.asr.model`がID（例`large-v3-turbo`）の要求は常に`context_conflict`→`unsupported_setting`で
推論前に失敗した（Windows実機受入、`fc71099`）。現在の不変条件:

1. `explicit_config`は最初に`asr_model`が当該backendの**静的カタログの正規ID**と完全一致することを
   確認する（path・Hub repository・未知名・`auto`・前後空白は`invalid_context`）。
2. `asr_model_dir`は構造だけを検査する: 絶対path、前後空白なし、NULなし、`..`なし、
   `str(Path(v)) == v`（正規化済み）。違反は`invalid_context`。expanduser・resolve・cwd・環境変数は使わない。
3. 純粋な`adapt_settings`は従来どおりIDだけを受け取り、presetのIDと比較する（不一致は
   `context_conflict`→`unsupported_setting`）。adapterはpathを見ない。
4. 比較成功後、runtimeの明示境界`bind_local_model`が実行用`Config.asr.model`を`asr_model_dir`へ
   置き換える。IDをリモート解決・catalog downloadへ変換しない。
5. `offline_preflight`がその絶対ディレクトリの存在と`model.bin`/`config.json`/`tokenizer.json`/
   `preprocessor_config.json`を確認する（欠落は`model_missing`、core preflight前）。これは必要条件の
   事前検査で、CTranslate2のロード成功（モデル固有の語彙ファイル等）を保証しない。実ロードはcoreが行う。

`stage_config_hashes`は実行用Config（`asr.model`=ディレクトリ）から計算するため、同じsnapshotの再試行は
同じhashでresumeし、別ディレクトリの結果を再利用しない。ディレクトリやモデルrevisionの変化はB7の
pre-start再検査（`candidate_changed`）で投入前に止まる。`asr_model_dir`はprivate IPCだけに存在し、
公開v1 view・SSE・console・diagnosticsへ出さない（`IPCModel`のreprとpublic projectionは値を含まない）。

互換性: B3の要求生成元はリポジトリ内のB7 dispatcherだけで、以前の「IDだけ・ディレクトリなし」の要求は
`offline_preflight`で必ず`model_missing`になり実行不能だった。そのため`asr_model_dir`をrequiredとし、
protocol v1のまま追加した（旧版workerはunknown fieldとして、旧版親の要求は欠落として、どちらも
schemaで拒否され、fail closed）。

## `prompt_compiler`（0.1.53）

`WorkerExecutionContext.prompt_compiler`（`b2-verbatim` | `glossary-bias-1`、省略時`b2-verbatim`）は、B7がsnapshotの
`ResolvedSpec.prompt_compiler_version`をそのまま送る値。B3は`adapt_settings`でこの版の`compile_prompt`を使い、`initial_prompt`と
用語集から実行用`Config.asr.initial_prompt`を作る。`b2-verbatim`で用語集があれば`unsupported_setting`、未知の版はschemaで拒否。
このfieldを知らない旧workerは`extra="forbid"`で要求全体を拒否する。詳細は[../backend/engine-adapters.md](../backend/engine-adapters.md) §7。

## `output_stem`（0.1.54）

`WorkerExecutionContext.output_stem`（省略可）は、B7 dispatcherが確保したジョブ専用ディレクトリの名前。あるときB3は
`base_config.general.output_dir`の**名前がこのstemと完全一致**することを要求し（違えば`invalid_context`）、coreの`export_all`へ
明示stemとして渡す。無いときは従来どおりcoreの命名（入力stem＋連番、上書きなし）。詳細は[../b7/output-directory.md](../b7/output-directory.md)。

## handshake・順序

最初はsequence=0のhello、能力single/cancelとprofileを返す。要求前なのでhelloの3 IDはnull。
親は版・profile・能力・sequenceを確認し、原則10秒以内にhelloがなければ失敗。成功後だけ要求を送る。
以後のeventはsequenceを1ずつ増やし、下記の受理前拒否を除いて要求の3 IDをすべて一致させる。

`accepted → progress* → completed|error` が基本。構造検証済みの拒否ではaccepted前のerrorも許す。
要求検証が失敗してIDを確定できない場合、hello直後（sequence=1）・accepted前に限り、
3 IDがすべてnullのWorkerErrorを受理する。許容codeはmalformed_message/schema_mismatch/
protocol_mismatch/oversized_message/unexpected_eofの5種類だけ。最後は空入力・改行前EOFを含む
framing拒否であり、キャンセルを意味しない。元のcodeを保ったterminalとして確定する。
core_failure/cancelled等の実行結果をIDなしで申告することは許可しない。

accepted後はerrorも3 IDの完全一致が必須。accepted前でもIDを指定したerrorは完全一致を要求し、
部分的なnullはDTOで拒否、不一致IDは状態検証で拒否する。hello前のerror、sequence違反、
terminal後の追加event・重複terminalは常に拒否する。
WorkerEventはshape/ID組合せ、framingはbyte/JSON/version、EventSequenceは段階・順序・ID照合と
匿名拒否codeの制約を所有する。worker emitterは未検証IDをraw requestから拾い直さず、
検証前の拒否にnull IDを付ける。WorkerClientはterminal・退出を確認し、同じreason codeを
値のないWorkerClientErrorへ変換する。DTO・wire形式を変えない訂正なのでprotocol v1を維持する。
progressは固定stage(audio/asr/diarization/merge/export/job)、state(running/skipped/validated)、
工程内fraction(0..1/null)のみ。message/details/raw logは捨てる。
`job/validated`は実行前検証の完了を示す。完了後のevent、重複terminal、再hello、accepted重複は拒否。

completedはcore_job_id、export成果物とmanifestのpath/size/hashをprivateに返す。
親はEOFまで検査し、exit 0、terminal 1回、ID、成果物hash、manifest版/job/input identity、
全5stage done、export一覧・出力directory・形式を確認する。EOF/exit 0のみでは成功にしない。
公開callbackはallowlist projectionのみ。completedのprivate metadataをSSEへ転送する設計ではない。

## キャンセル・timeout

stdin control readerが別threadでCancelTokenへ伝達し、cancel_acknowledgedを返す。
受理はcore停止・lock整理完了の証拠ではない。coreが境界でCancelledErrorを返し、finallyが
完了してからerror(cancelled)となる。完了との競合では、coreが完了済みならcompletedもあり得る。
親は自身がcancelを送っていないcancelledを正常なユーザー中断と解釈しない。
EOFはcancelを要求するが、terminalはunexpected_eof。異常をcancelledへ置換しない。

- handshake: 原則10秒、要求は送らずtree cleanup。
- IPC write/terminal後のEOF・終了待ち: 原則10秒。サイズ上限と独立。
- 検証: 原則30秒のsoft limit。job/validatedまでを計測。
- 全処理deadline: 明示された場合だけ。進捗の沈黙によるidle killはない。

検証/全処理の期限超過は一度cancelを送り、coreの安全な境界での退出を待ってから
validation_timeout/process_timeoutを報告する。native呼出しが応答しなければ待機は延びる。
有限時間でのhard cancel・pauseは未対応で、期限をhard SLAとして扱わない。
protocol破損・起動/通信異常時の強制tree cleanupはインフラ中断であり、成果物整理完了や
cancelled成功を保証しない。再実行時は既存coreのreconcile/lock検査が必要。

## エラーコード

`WorkerErrorCode`が正本。起動系はstart_failed/profile_invalid/profile_unavailable/
profile_incompatible/module_missing、通信系はhandshake_timeout/protocol_mismatch/
schema_mismatch/malformed_message/oversized_message/invalid_sequence/id_mismatch/
unexpected_eof/io_timeout/worker_crash、処理系はvalidation_timeout/process_timeout/
cancelled/core_failure/invalid_context/unsupported_setting/model_missing/dependency_missing/
artifact_invalid。メッセージはcodeだけで、元例外やValidationErrorのinputを返さない。
CLIへの自動fallback・要求の自動再投入はしない。

```console
python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json
python -m utteran_contracts.worker_schema --output docs/architecture/b3/worker.schema.json --check
```
