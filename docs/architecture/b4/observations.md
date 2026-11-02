# 観測・鮮度・privacy

software_support / hardware / runtime / venv / model / native / smoke / execution_path /
memory / diarizationを別Observationとして保持する。sourceとreasonは固定allowlist。
stateはpassed/failed/unknown/not_applicable。native不要、話者分離無効だけnot_applicableをcoreが生成。
必須Observationのnot_applicableはunknown判定となり、passedに読み替えない。
software_supportとexecution_pathはrecipe/settingsからpureに判定し、それ以外は取得証拠に基づく。
OS自体が未観測の場合もunknownであり、既知の不適合と混同しない。

## 判定と証拠の条件

- ready: hard条件が一致し、全必須Observationが新鮮なpassed、identityが完全、
  対象settings/route/model/profile/runtime/hardwareに対応するsmokeがあり、memory条件も満たす。
- blocked: fresh failed、software/transport/固定設定の不適合、明示memory不足がある。
- unknown: 観測なし、不完全identity、期限切れ、fingerprint相違、smoke欠落、memory不明。
  blockedの明確な理由が同時にあればblockedを優先する。

`normalize_observation()`はobserved_atなし、未来、expires_atなし、期限切れ、fingerprint相違を
unknownへ戻す。元のobserved_at/fingerprintを維持し、最新と偽らない。
期限のない証拠はcache_stale。元のprobe_timeout/probe_error等はdeployment.observationsにも残し、
candidate.observationsには正規化後の失効理由を持つ。not_applicableはそのまま別状態。
resolve時にも再評価するため、古いsnapshot内のrunnable=readyをそのまま信用しない。

`evidence_fingerprint()`はExecutionIdentityとProcessingSettings全体のcanonical JSON SHA-256。
profile ID/generation/path-extras-lockのdigest、OS/Python ABI、runtime依存/driver、hardware/index、
model revision/required file identity、native manifest/build、recipe/adapter/smoke条件版、
transport、pyannote component、全設定が異なると証拠を共有しない。identity欠損のまま全passedでもready不可。
providerはruntime/hardware fingerprintに実際のOS/driver/dependency/device identityを含める責務を持つ。
既存`devices.device_probe_fingerprint()`を明示probe providerで再利用できるが、呼出しがdriver検出を
起動するためpure resolve/標準metadata collectorからは呼ばない。

smokeが新鮮なpassedの場合だけrouteをverified/unverified/mismatchへ分類する。
必須実測が不足すればsmoke_route_unverifiedでsmoke unknown、矛盾すればsmoke_route_mismatchでfailed。
別Observationに明確なfailedがあればcandidateはblockedを優先する。失効済みsmokeはroute照合で復活しない。
backend-defaultは実測不要。selected-deviceはidentity.deviceへ具体化し、gpuとopenvino-gpuは同一視しない。
encoder/decoder_observedが必須componentの実測値。native recipeはinitialized_route.native_variantも必要。
initialized_routeだけでcomponent実測を補ったり、要求routeを実測へコピーしたりしない。

accelerator componentにはencoder_device_index_observed / decoder_device_index_observedの実測も必要。
両者をidentity.device_indexへ厳密に照合し、未観測ならunknown、違えばblocked。
CPUはsoftware条件がindex 0のみを許す単一routeなので番号の実測は必須ではない。
OpenVINOとGGML/Vulkan等の番号はAPIごとの番号であり、同じ整数が同じ物理GPUとは推測しない。
providerは両engineの対象device/driverのmappingをhardware/runtime fingerprintへ拘束する責務を持つ。
実backendが必要なroute/indexを観測できない場合はunknownを返し、readyを主張してはいけない。
標準collectorはこれらのsmokeを生成しない。テストhelperだけが明示した合成実測を供給する。

Candidate IDはidentityのSHA-256から作るopaque ID。candidate revisionは元deployment/evidenceと
設定scopeから導くdigest。パス・実行fileをIDから組み立てない。immutableなDTOを新規生成し、
新inventoryが以前のResolvedSelectionを書き換えることはない。

## 標準collectorの限界

現profileのsys.prefix、manifest、cwdのuv.lock、package metadata、managed model、native statusを読む。
manifest schema/lock全体SHA-256/extras/venv_pathを現GUIと同じ条件で判定する。
venv generationはmanifest内容/mtimeとprofile root ctimeのdigest、profile fingerprintには
path/extras/依存版/OS/ABIも含む。新たなgeneration管理storeはB8以降。
model revisionは`local-<file identity digest>`でありupstream Hub revisionとは異なる。
file identityはresolved path/size/mtime_ns/ctime_ns。content checksumや完全検証の証明ではない。
read-only native statusとmanifest/executableのidentityを分離する。

Hub cache探索、driver/GPU probe、IR変換、memory推定、pyannote依存解決、smokeを行わない。
標準collectorのhardware/runtime fingerprintはnull、smoke/memory/diarizationはunknownであり、
モデルとvenvが完全でもreadyにはならない。managed領域にないcache-onlyモデルもこのcollectorでは
未導入候補として扱う。追加観測は明示providerで差替可能。合成passedはテスト専用で実機証明にしない。
取得日時が判明しない旧device cacheを今の観測として再stampしない。

## 公開と読取専用

repr/strは値なし、public_projectionはcontract名/versionだけ。to_wireは**private**。
設定にPrompt等を含み得るため、evidence digestを含めて通常log/公開SSEへ出してはいけない。
公開DTO metadataにはtoken、音声、文字起こし、絶対path、raw stderr/tracebackを含めない。
橋渡しはstdin、固定module、shellなし、UTF-8、サイズ制限、固定エラーだけを使用する。
model download、uv sync、venv新設、native build、manifest rewrite、cache更新、logging初期化なし。
テストのaudit hookはimport/schema/collection/resolveの子process・network・書込みを拒否して検査する。

## PR #25 P3: 読取中の上限と専用環境

`capability_transport.read_bridge`はbinary/unbuffered Popenのstdout/stderrを独立threadで並行drainし、
stdinも別threadで期限内に送る。stdoutは読取中に8 MiB、stderrは64 KiBを検査し、1 byteでも
超過した時点で固定bridge_stdout_limit/bridge_stderr_limitを返す。stdout bufferは8 MiB以内、
各readerの一時chunkは最大8 KiB。stderr本文は蓄積しない。最終bytes化/UTF-8 decode/JSON parseの
追加メモリは存在するため、8 MiBはプロセス全体のRSS上限ではなく保持する生stdoutの上限。
切詰めたJSONを成功扱いせず、正常終了・全pipe終了と厳密UTF-8を確認してからparseする。
固定reasonは超過/timeout/異常終了/不正JSON・UTF-8/IO障害を区別し、raw出力を含めない。

既存ProcessSupervisorのWindows Job ObjectとPOSIX新sessionを再利用し、終了時には子をwaitして回収。
POSIXはleader終了後もprocess groupを停止、WindowsはJob Objectのcloseで残る子孫も停止する。
無応答・blocked stdin・子孫がpipeを保持する場合もdeadlineで停止し、pipe/threadを回収する。
既存B3 worker/process primitivesの実装自体は変更しない。

bridge専用environmentはOS変数名のallowlistから構築し、CliAdapter.environment/session token取得を呼ばない。
WindowsのSystemRoot/WINDIR/SystemDrive/PATH/PATHEXT/TEMP/TMP、user/application/program data roots、
POSIX/macOSのHOME/TMPDIR、locale/TZ、XDG data/config/cache roots、LD/DYLD library探索pathを保持する。
UTTERAN_DATA_ROOTだけはmanaged model/nativeの参照先に必要なので保持する。
UTTERAN_PROFILE、UTF-8、HF offline/telemetry無効化の非秘密flagは固定値で追加する。
HF token、Hugging Face token、AWS/Azure/GCP/OpenAI credentials、session token、proxy credentials、
PYTHONPATH/PYTHONHOME、任意のUTTERAN_*や未知変数は継承しない。実装の固定allowlistを正本とする。
`-I`だけに秘密除去を任せず、-I/-B/絶対Python/固定module・action/shell=Falseを維持する。
