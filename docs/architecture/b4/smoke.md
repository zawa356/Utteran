# B4 ready/smoke — 明示smoke・private evidence・読取連携（0.1.50）

更新: 2026-11-12 JST（開発版`0.1.50`、未リリース、Draft PR）。基点`main` `9237040`（PR #46統合、0.1.49）。

> **これはCloud開発工程の成果物である。** 実GPU・実モデル・Windows実機での成功は本書のどこでも主張しない。
> CIの成功は「合成fixtureで規則が守られる」ことの証拠であり、実機受入ではない。Cloudで`ready=0`は正常な結果。
> 実機での確認手順は[smoke-local-acceptance](smoke-local-acceptance.md)（**未実施**）。

## 1. Gap調査（着手時の事実）

| 項目 | 着手時の実装 | 不足 |
|---|---|---|
| 標準collector（`collector.py`） | profile manifest・managed model・native manifestを読むだけ | `runtime_fingerprint`/`hardware_fingerprint`はnull、smoke/memoryはunknown。readyは構造的に0 |
| bridge（`bridge.py`） | `recipes`/`refresh`/`resolve`のmetadata-only子process | smokeの実行器ではない（そうしてはならない） |
| evaluator | `evidence_fingerprint()`・`_verify_smoke_route()`・`evaluate_candidate()`で厳格判定 | 判定規則は十分。証拠を供給する経路が無い |
| B7 | `resolve`と`prestart_check()`がそれぞれbridge `resolve`を新規に呼ぶ | 1回のrequest内の証拠は次のprocessへ持ち越せない → 永続のprivate evidenceが必要 |
| B3 worker | 完了時にartifact/manifestだけ返す。実routeを返さない | worker protocolへroute報告を足すとB3 schema変更になる |

### 1.1 実routeを何から観測できるか（faster-whisper / CTranslate2 4.8.1）

- `faster_whisper.WhisperModel`はロード済みの`ctranslate2.models.Whisper`を`.model`に持ち、CTranslate2は
  **replica poolの実際の`device`（`"cpu"`/`"cuda"`）・`device_index`（list[int]）・`compute_type`（実効値）**をproperty として返す
  （開発環境でattribute存在を確認済み）。CTranslate2自体はdeviceを暗黙に切り替えず、明示deviceで作れなければ例外になる。
- **制約（そのまま記録する）**: CTranslate2のWhisperは1 replicaでencoder/decoderを同じdeviceで実行し、**componentごとの実行deviceを別々には報告しない**。
  本実装はロード・推論完了**後**にruntimeが返すreplicaの値を、encoder/decoder両方の観測値として記録する。
  要求値・設定値・recipeの期待値をコピーしたものではないが、「componentごとに独立に計測した値」ではない。
  **owner決定（2026-11-12、PR #47レビュー）**: この値を、fw-cpu/fw-cudaのencoder・decoderに共通する実行deviceの根拠として使う設計を承認。
  これは独立した2点の観測ではなく、**共通のモデル実行先から派生したattestation**である。whisper.cpp・OpenVINO等のcomponent分離配置には一般化しない。
  また、モデル全体のdevice情報は、個々のCUDA kernelがすべてGPUで実行されたことの証明ではない。
- `compute_type`の実効値が要求と異なれば（CTranslate2の暗黙変換）、smokeを`failed`（`compute_mismatch`）とし証拠に採用しない。
  例外は1つだけ: CPUで要求`int8`はCTranslate2が実効値`int8_float32`として返す（Cloud Linuxで実測、下記§7）。この別名だけを明示表で許容し、
  CUDAを含む他の差は全て`compute_mismatch`。
- 既存`FasterWhisperBackend.load()`は`device="auto"`のときだけCPUへfallbackする。smokeは常に明示device（`cpu`／`cuda:<index>`）で呼ぶため
  fallback経路に入らない。明示`cuda:N`が使えなければ既存実装が`BackendUnavailableError`を投げる（自動fallbackなし）。

### 1.2 identityと必要/利用可能メモリ

| 証拠 | fw-cpu | fw-cuda |
|---|---|---|
| runtime fingerprint | profile内のpackage metadata（ctranslate2/faster-whisper/tokenizers/av/onnxruntime/numpy/huggingface-hub）＋Python版/ABI＋OS release/machine。smoke時は実importした`ctranslate2.__version__`/`faster_whisper.__version__`とmetadataの一致も検査 | **null**。CTranslate2はdriver/CUDA runtimeの版を公開しない。metadata経路からdriver probe（nvidia-smi等）を起動しない |
| hardware fingerprint | CPU名（Linux `/proc/cpuinfo`、Windows registry `CentralProcessor\0`）＋machine＋論理CPU数 | **null**。CTranslate2はGPU名・UUIDを返さず、CUDA ordinalはNVML/他APIの番号と一致する保証がない |
| required memory | smoke processの実測ピーク（既存`memory.measure_peak`の100ms標本＋POSIXのru_maxrss）。**短尺fixtureでの下限値**であり長時間音声の保証ではない | **null**（VRAM使用量を観測するAPIが無い） |
| available memory | smoke開始直前の物理メモリ空き（既存`memory.read_memory("cpu")`） | **null** |

取得できないものは`null`/`unknown`のまま。CUDAは「smoke（実route）は記録できるがidentity・memoryが不足」になり、
**evaluatorにより`unknown`（`evidence_scope_incomplete`・`memory_unknown`）**となる。これは後続の実機受入に残す。

### 1.3 証拠の持ち越し

明示smokeの結果だけを、開発用data root配下の**version付きprivate evidenceファイル**として保持する（§3）。
後続の`refresh`/`resolve`（＝B7のadmissionとprestart）はこれを**読むだけ**で、その時点で新しく算出したidentityと
`evidence_fingerprint()`が厳密一致した場合にだけ観測へ統合する。新しいDBは作らない。

## 2. 責務境界

```text
開発者 ─ python -m utteran_gui.capability_smoke （GUI venv、coreをimportしない）
          │ 1. 配布/凍結を拒否、UTTERAN_DATA_ROOTのmarker必須（作成しない）→ validate_dev_data_root
          │ 2. 同一rootのGUIインスタンスロックを取得（GUI稼働中・別smoke実行中は拒否）
          │ 3. 一時作業dir作成 → 既存bounded transport（read_bridge）で単発子processを起動
          ▼
profile python -I -B -m utteran.capabilities.smoke run   （stdinにprivate request、argvに秘密なし）
          │ offline env強制・logging無効化 → 標準collectorで対象deploymentを作り事前検査
          │ scopeのpending記録 → FasterWhisperBackend.load/transcribe（合成WAV）→ runtime読出し
          │ 最終記録（atomic replace）→ stdoutへ固定codeだけ
          ▼
<root>/cache/capability-evidence-v1/<scope>.json   （private、smokeだけが書く）
          ▲ 読取のみ
bridge refresh/resolve（local_provider）→ apply_smoke_evidence → make_inventory/resolve（pure）
```

- `utteran.capabilities.bridge`は変更しない（metadata-onlyのまま）。smokeは別module・別action。
- 通常のGUI起動・旧API・B7 admission/prestart・HTTP GETはsmokeを起動しない。B7は従来どおりbridge `resolve`を呼ぶだけ。
- `make_inventory()`/`resolve()`の純粋性、B7の`prestart_check()`/再resolve、固定候補の不一致時拒否（fallbackなし）は変更しない。

## 3. private evidence形式（`utteran-capability-smoke-evidence` v1）

- 場所: `DataPaths.capability_evidence` = `<UTTERAN_DATA_ROOT>/cache/capability-evidence-v1/`。
  **`UTTERAN_DATA_ROOT`が無い既定レイアウトでは`None`**（読まない・書かない）。読取・書込みともG-09の開発marker（完全一致・非symlink）を要求する。
- ファイル名: `<scope>.json`（scope=`evidence_fingerprint(identity, settings)`、64 hex）。1 scope 1ファイル。
- 内容（全fieldが必須、未知field拒否）: `format`、`version`=1、`scope`、`recipe_id`（fw-cpu/fw-cuda）、`state`（pending/passed/failed）、
  `outcome`（固定code）、`observed_at`/`expires_at`（UTC offset付き）、`encoder_observed`/`decoder_observed`、
  `encoder_device_index_observed`/`decoder_device_index_observed`、`required_memory_bytes`/`available_memory_bytes`、
  `fixture_sha256`、`checksum`（他fieldのcanonical JSONのSHA-256）。
- **保存しないもの**: token、絶対path、ユーザー録音、文字起こし、prompt・設定本文、raw stderr、traceback。settingsはscope digestにだけ反映する。
- TTL: `observed_at`から24時間（`expires_at - observed_at`が24時間を超える記録は不正として拒否）。読取で延長しない。
- 上限: 1ファイル8 KiB、1 directory 64件（新しいscopeが上限を超える場合は書かずに`evidence_limit`）。
- 書込み: 同じdirectoryの一時ファイル（`O_EXCL`、0600）→fsync→`os.replace`。directory・既存ファイルがsymlinkなら拒否。

### 3.1 採用条件（どれか1つでも欠ければ不採用＝smoke_missing）

存在する／regular file（symlink・directory不可）／8 KiB以下／UTF-8 JSON（重複key・NaN拒否）／schema一致／format・version一致／
checksum一致（部分書込み・破損の検出）／ファイル名とscope一致／要求scopeと一致／`state=passed`／`observed_at <= now < expires_at`／TTL上限内。
不採用のファイルは**削除も修正もしない**。改竄の扱い: checksumは偶発的破損・部分書込みを検出するが、data rootへの書込権を持つ者による意図的な
書換えは防げない（その者はprofile venv自体も書換えられる）。この信頼境界をそのまま記載する。

### 3.2 失効

scopeはExecutionIdentity全体（profile generation/fingerprint、model revision・file identity、runtime・hardware fingerprint、
device/index/compute、recipe/adapter/smoke版、execution path）とProcessingSettings全体のdigestなので、どれかが変われば別scopeになり古い記録は一致しない。
さらに読取時に`normalize_observation()`がfingerprint・時刻を再検査する。

### 3.3 再smoke

事前検査を通ったsmokeは推論の**前**に同じscopeへ`state=pending`を書く（atomic replace）。成功時だけ`passed`へ置換し、
失敗・例外時は`failed`へ置換する。processがkill/timeoutされた場合はpendingが残り、いずれも採用されない。
pending自体を書けない場合は既存の同scopeファイルの削除を試み、`evidence_write_failed`で終了する。

## 4. observationへの統合

`apply_smoke_evidence()`は`fw-cpu`/`fw-cuda`かつexecution path `worker`のdeploymentについて、新しく算出したruntime/hardware fingerprintで
identityを作り直し、そのscopeの記録が採用条件を満たすときだけ、そのidentity＋次の観測でdeploymentを置換する（不採用なら元のまま）。

| kind | 値 |
|---|---|
| venv/model/native | collectorの観測（fingerprintだけ新identityのscopeへ付替え、時刻・期限は不変） |
| smoke | `state=passed`、source `smoke`、記録の観測値・番号・時刻・期限。route判定はevaluatorの`_verify_smoke_route()` |
| runtime/hardware | fingerprintが算出できたときpassed（時刻・期限は記録と同じ）、nullならunknown `not_observed` |
| memory | 両値ありでpassed（不足ならfailed `memory_insufficient`）、どちらかnullならunknown `memory_unknown` |

## 5. 明示smokeの実行制御

- 入口: `python -m utteran_gui.capability_smoke --profile cpu --recipe fw-cpu --model <catalog id> [--timeout 900] < settings.json`
  （stdinはProcessingSettingsのJSON、上限1 MiB。正確な手順は[smoke-local-acceptance](smoke-local-acceptance.md)）。
- 拒否（固定code、終了code 2）: 配布/凍結、root未指定・相対・不在・予約領域、**marker無し**（空dirでもmarkerを作らない）、同一rootでGUI稼働中または別smoke実行中（`gui_already_running`）、
  profile未導入、request不正。別のdata rootは互いに独立。CLI・旧バイナリ・非協調processは排除しない（G-09と同じ範囲）。
- 子process: 既存`read_bridge`（ProcessSupervisor、POSIX新session/Windows Job Object、stdout 8 MiB・stderr 64 KiBの読取中上限、
  timeout時の子孫停止とwait）と`bridge_environment()`の同じallowlist。timeout既定900秒・上限3600秒。shellなし、argvは固定module＋profile/recipe/model id。
- 子は`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/`HF_HUB_DISABLE_TELEMETRY`を自身でも強制し、Hub ID解決をしない（catalogからmanaged pathだけ、
  `local_complete_model`必須）。sync・venv作成・download・変換・native buildは行わない。loggingは`logging.disable`で全停止（tracebackを出さない）。
- 一時成果物: 合成WAVは親が作る一時dirへ書き、親が子のwait後に削除する。evidence directoryの`.<scope>.<random>.tmp`も親が子の終了後に掃除する。
- 合成fixture: コード生成の16 kHz mono 16-bit PCM 3秒（決定的な正弦波合成、音声・個人情報なし、SHA-256固定）。VADは無効にして
  encoder/decoderが必ず実行されるようにする（無音をVADで全除去すると推論が走らないため）。文字起こし結果は数えるだけで保存・出力しない。
  smokeはroute・ロード・推論完了の確認であり、品質・export・全オプションの実行を証明しない。

## 6. 既知の限界・owner判断

1. **encoder/decoder個別attestation**: CTranslate2はreplica単位の値しか返さない（§1.1）。共通モデル実行先からの派生attestationとして両component観測に使うことはowner承認済み（PR #47）。分離配置のrecipeには一般化しない。
2. CUDAのidentity（driver・GPU UUID・番号対応）とVRAMは未観測 → fw-cudaは本実装では常に`unknown`。
3. required memoryは短尺fixtureのピークで、将来の任意の音声長に対するメモリ保証ではない。available memoryはsmoke時点の値で、refresh時に再測定しない（TTLで鮮度を制限）。
4. settings全体がscopeに入るため、smokeはジョブと**同じeffective settings**で実行する必要がある。
5. 証拠はmarker付き開発rootにしか置けない。配布版・既定レイアウトでは常に`ready=0`のまま（意図どおり）。
6. **B3の既存条件によりworker対象モデルが限られる**: B3/B4は`preprocessor_config.json`を必須とする。catalogの`tiny`/`base`/`small`/`medium`
   （Systran配布）はこのファイルを含まないため、smoke対象は実質`large-v3-turbo`/`large-v3`/`kotoba-whisper-v2.0`（HF APIのファイル一覧で確認）。本PRでは条件を変えていない。

## 7. Cloudでの確認範囲（2026-11-12、Linux container、**実機受入ではない**）

- 合成回帰試験（モデル・GPU・networkなし）: `tests/test_b4_smoke_evidence.py`（形式・fail-closed読取・失効・overlay）、
  `tests/test_b4_smoke_executor.py`（子processのorchestration・fake runtime・固定code・offline/無出力・開発入口の拒否・ロック排他・
  timeout時の子孫回収）、`tests/test_b4_smoke_b7.py`（B7 admission→bridge→`local_provider`の実経路、prestartでの失効・変更・再smoke失敗、
  fresh processのrefreshが書込み・spawnしない）。overlayを外すとB7試験6件がRedになることを確認。
- 実runtime経路のコード確認（開発者準備のscratch領域、製品経路ではない）: Hubから取得した`tiny`に手書きの`preprocessor_config.json`を加え、
  開発用.venv（CTranslate2 4.8.1 / faster-whisper 1.2.1）で`run_smoke`＋実`FasterWhisperSmokeRuntime`を1回実行。初回は`compute_mismatch`
  （`int8`→`int8_float32`）で正しく拒否され、上記の別名を追加後に`passed`、続くoverlay＋evaluatorで`ready`となった。
  これは「read-backのコードが動く」ことの確認であり、Windows・実モデル（large-v3-turbo）・CUDAでの成立を示さない。
