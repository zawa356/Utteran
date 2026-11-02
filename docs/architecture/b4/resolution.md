# Policy解決

`resolve(inventory, Policy, now=aware_datetime)`はpureであり、refresh/実行/clock読取を行わない。
InventorySnapshotには要求ProcessingSettings、対象execution_path、呼出し元の単調revisionを持つ。
B4はrevisionを永続採番しない。要求値を具体設定に書き換えず、selected候補内に具体構成を保持する。

## Hard filter

recipe/OS/device/compute/family、固定backend/model/compute、inactive namespace、language/diarization、
timestamp制約、workerの限定条件、全必須観測、profile generation、model/native/runtimeとsmokeの
scope/freshness、観測memoryを検査する。allowed_profile_idsが空ならinventory内の全既存profile。
空集合はprofile新設の許可ではない。明示max_memory_bytesは必要量に適用する。
memory必要量/利用可能量は対象構成全体と対象device資源をproviderが観測する。未観測はunknown。
execution_deadline_secondsのhard保証はB4にないため、指定時はdeadline_unverifiedで除外する。

固定backend/modelに合わないdeploymentはCandidateを生成せず、InventoryExclusionへ具体identityと
理由を記録する。ResolvedSelection.exclusionsも保持するため、候補ゼロの理由を失わない。

## Auto

readyかつhard filterに適合する候補だけをrankする。
benchmarkを使うのはspeed/balancedで、**全適合候補**に新鮮でscope一致する測定があり、
fixture hash、duration、language、model revision、precision、timestamp、diarization、threads、
warmup、runtime versionの比較keyが全て一致するときだけ。elapsed_seconds昇順→candidate ID順。
異groupの勝者同士を直接比較しない。scope/time/model/precision/settingsも検査し、smokeを速度値にしない。
quality objectiveは速度測定を品質根拠にせず静的ルールを使う。
履歴/reliability storeは未実装であり、存在しない履歴や成功率を順位根拠にしない。

比較可能な実測がなければB4静的順序
`fw-cuda → wc-vulkan → wc-ov-vk → wc-ov → genai-ov → fw-cpu → wc-cpu`を使い、
同順位はopaque candidate IDの辞書順。入力順に依存しない。
この順位は選好にすぎず、実速度/品質の優位性を主張しない。既存CLI autoとは別のルールである。
静的選択のconfidenceはlow、比較可能benchmark使用時のみmedium。highは生成しない。

ready候補ゼロならselected=null、error=no_runnable_candidate。各alternativeの具体候補、
eligible、理由code、全観測を保持する。Setup Planの生成はB8へ委ねる。

## Fixed / fallback / 凍結

fixed_candidate_idがreadyかつ全制約に一致する場合だけ選択。消失、stale、blocked、許可外なら
fixed_candidate_unavailable。B1 Policyがfallback=noneを要求し、CPU等へ切り替えない。

結果はrequested_settings、Policy全体（id/revision含む）、inventory_revision、resolved_at、
selected/preapproved、alternatives、理由、evidence_kinds、confidenceを不変DTOとして返す。
selected/preapprovedにはcandidate revision、profile generation、model revision、device/index/compute、
requested route、execution path、実route観測、全fingerprint/evidenceを含める。
auto/fallback=preapproved時だけ順位順の残りready候補を凍結する。fallback=noneなら空。
**B4はfallbackを実行しない**。job snapshot/token/transaction/revision再確認、attempt/stage invalidationはB7。
実初期化routeとencoder/decoder observedはsmoke providerが明示した値だけ。要求routeを成功値へコピーしない。
