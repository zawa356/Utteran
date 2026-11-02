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

全wire envelopeで`schema_version:1`と`protocol_version:1`を必須とする。
Python構築時は既定1を提供するが、framingは欠落・bool・文字列も拒否する。
JSON Schemaでもwire版をrequiredにしている。B1 ProcessingSettingsのschema版も検証する。

## DTOと要求

[worker.schema.json](worker.schema.json)がオフライン生成物。

- `WorkerRequest`: type=execute、request/job/attempt ID、profile、input_path、B1 settings、
  WorkerExecutionContext、mode=single、resume、force。
- `WorkerExecutionContext`: base_config、asr_backend/model/device、diarization_device、
  compute_type、optional native_variant。core dataclassとは別のtransport型。
- `WorkerCancel`: type=cancel、同じ3 ID。1 workerへ2回目のexecuteを送れない。
- `WorkerHello`, `WorkerAccepted`, `WorkerProgress`, `WorkerCompleted`, `WorkerError`,
  `WorkerCancelAcknowledged`: discriminated event union。
- `WorkerArtifact`: private path、byte size、SHA-256。入力・本文を通常eventへ載せない。

IDはASCII英数字で始まる最大128字の英数字/underscore/hyphen。pathはIDとして使わない。
profileはcpu/cuda/intel/vulkanの登録enum。base_configの全nested keyはcoreモデルのfield一覧で
検査し、extra=ignoreに任せない。ffmpeg executable、native/venv差替え、raw log指定は拒否。
input/output/job directoryは絶対pathを要求する。Config.loadを呼ばない。

## handshake・順序

最初はsequence=0のhello、能力single/cancelとprofileを返す。要求前なのでhelloの3 IDはnull。
親は版・profile・能力・sequenceを確認し、原則10秒以内にhelloがなければ失敗。成功後だけ要求を送る。
以後のeventは要求の3 IDをすべて一致させ、sequenceを1ずつ増やす。

`accepted → progress* → completed|error` が基本。構造検証済みの拒否ではaccepted前のerrorも許す。
未検証requestには3 ID=nullのerrorを返すが、既知requestの親はそのeventをID不一致として拒否する。
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
