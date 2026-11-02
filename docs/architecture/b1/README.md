# Phase B1 — 共通DTO・オフラインSchema Export

B1は将来の共有データ契約だけを追加する。**新API、UI、worker、実行/選択engine、永続queue、ストア、migrationは未実装**。既存CLI/API/GUIは新DTOへ切り替えていない。

基点は未mergeのPhase A `docs/phase-a-architecture`、HEAD `60d9bdd6006551149c6dde5a735dee17c16cc15d`。レビュー時HEADからの更新なし。mainは`1097aeffe9c8562aaf6b12f2e4a1bd37de8888b6`。B1 branchは`feat/b1-contracts`、stacked PRのbaseはPhase Aを指定する。PR #23は混合していない。

## 配置と依存

`src/utteran_contracts`は同じ`utteran` distribution内の独立import package。wheelの対象へ追加し、mypyでも検査する。別distribution/別wheelはB1で導入しない。runtime依存は既存Pydantic 2と標準libraryだけで、core/GUI/heavy libraryをimportしない。実model・GPU・filesystem scan・subprocess・networkは不要。devにjsonschemaを追加し、Phase A YAML比較に使う既存transitive PyYAMLを明示宣言した。既存packageのversionは更新していない（製品versionだけrelease規約で0.1.30）。uvのlock形式revision/orderの更新は使用したuv 0.12.23の出力で、手編集していない。

`utteran_gui`からもcoreからも独立してimportできる。現GUIのsource/起動/配布specへの新importは追加しない。未使用contractsをPyInstaller hiddenimportsへ無条件に追加せず、後続のGUI接続時に通常のimport追跡と禁止module guardを使う。wheel自体は従来どおりcoreも含むが、import境界を分離する。これはGUI frozen bundleへcoreを含める方針ではない。

## 実装したPhase Aスキーマ（21/74）

| 領域 | DTO |
|---|---|
| 共通 | `Ref`, `Issue`, `ErrorDetail`, `Error` |
| ASR | `VadSettings`, `FasterWhisperSettings`, `WhisperCppSettings`, `GenAISettings`, `ExtraArguments`, `ASRSettings` |
| 設定 | `DiarizationSettings`, `PostprocessingSettings`, `OutputSettings`, `ProcessingSettings` |
| preset | `PresetWrite`, `Preset` |
| policy | `PolicyWrite`, `Policy` |
| editing | `SettingOverride`, `FileInput`, `EditingSession` |

補助基底は`Contract` / `VersionedContract`、契約versionは`SCHEMA_VERSION=1` / `SchemaVersion`。これは製品version、HTTP API major、worker protocol、core結果schema versionと別である。
Phase Aの形を維持し、schema_versionはProcessingSettings/Preset/Policy/EditingSession/Error等のversion付きrootに必須。
RefやASRSettings等のnested型はrootのversionで解釈し、独自のversion fieldを付け足していない。Phase Aの残り53 schema（ResolvedSpec/JobSnapshot/SetupOperation/Queue/Event等）は未実装のProposedで、[coverage](coverage.md)に列挙する。

## 利用と不変性

```python
from utteran_contracts import SettingOverride, VadSettings

vad = VadSettings(enabled=False)  # threshold省略を保持
assert vad.model_dump() == {"enabled": False}
prompt = SettingOverride(path="/asr/initial_prompt", value=None)  # 明示null
changed = vad.model_copy(update={"enabled": True})  # 検証した新しい値
```

全DTOはfrozen、配列はtuple、speaker_labels/override内objectはコピーしたread-only mapping。`model_copy(update=...)`も再検証し、元の値を変更しない。通常の代入/append/map更新を拒否する。Pydanticの`model_construct`/`object.__setattr__`は検証を迂回できるPython内部APIであり、外部入力へ使わない。既に検証したfrozen DTOの組み立ては許可し、JSON/Mappingからの入力は検証する。

省略optionalはPython attributeではNoneとなるが、`model_fields_set`で不在を区別する。`model_dump`/`model_dump_json`/`to_wire`は不在fieldを出力しない。optionalかつwire非nullableのfield（例threshold）へ明示nullは拒否。nullableなPrompt/vad_model等は明示nullを保持。false/auto/空Promptは入力どおり保持し、空Prompt→nullの正規化や継承/mergeはB2以降のcore adapterへ委ねる。required fieldはdefaultで補わない。unordered set/generator等から配列を作らず、JSON list/tupleだけを受け入れる。

SettingOverrideは型から作るleaf registryでJSON Pointerを検査。配列/map全体はleaf、配列index・map key・親object・未知/不正escapeは拒否。path重複/親子競合は同scope内で拒否、common/file間の同pathは正規のoverrideなので許可する。valueはnullable/型/範囲/unique arrayを対象fieldから検証し、必ずJSON値。重複client_file_idも拒否する。実際のpreset適用やbackend/runtimeとの互換性は判定しない。

## 機密データと公開projection

Prompt/用語集/input path/追加args等の値はrepr/strへ出さず、通常診断は`public_projection()`（型名と契約versionのみ）を使う。認証tokenを格納するDTO/自由fieldはない。`to_wire`/`model_dump*`は**private export**で値を保持するため、ログへ渡さない。SecretStrのマスク済みJSONで再現性を失う方式は採らない。

validationのrendered errorは`hide_input_in_errors=True`。公開診断では`validation_error_projection(error)`だけを使用する。Pydanticの`errors()`/`json()`は明示的に取り出すとprivate input/contextを含み得るためログ・APIへ直接渡さない。未知field名やmap keyも利用者入力になり得るので、raw exception/tracebackを公開ログへ流す安全性を保証しない。B1はログsinkやAPI error handler自体を追加しない。

## 生成コマンド

依存はuv.lockのPydanticへ固定し、通常開発環境で実行する。export時にFastAPI appやruntime OpenAPI endpointを起動しない。

```console
uv sync --extra dev --extra gui --locked
uv run --no-sync python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json
uv run --no-sync python -m utteran_contracts.schema --output docs/architecture/b1/contracts.schema.json --check
uv run --no-sync pytest tests/test_contracts.py tests/test_contracts_schema.py tests/test_contracts_boundary.py
```

`--output`なしはstdout、`--check`は存在/完全byte一致を検査して変更しない。不一致はexit 1、誤った引数はexit 2。出力親directoryは明示的に用意する。import時のI/Oはなく、書込みはexport CLIを実行してoutput指定した場合のみ。UTF-8/LF、sort_keys、固定indent、時刻/絶対pathを含まない。生成物はDraft 2020-12の`$defs`と明示した7種類のrootを持つ。個別DTOの検証は`#/$defs/<name>`を参照する。これは全APIのOpenAPIでもTypeScript生成物でもない。

## Phase Aとの差異・整合性

手書き[OpenAPI](../phase-a/openapi.yaml)は変更しない。必須/optional、nullable、enum、数値・文字列・配列・辞書制約、version、additionalPropertiesを21 schemaについて比較する。title/description/default注釈、ref表記、required空配列の省略は意味差ではない。

| 差分 | B1の規約・理由・移行影響 |
|---|---|
| SettingOverride.path | Phase Aのprefix patternを登録leaf enumへ厳格化。本文のleaf限定方針を実装、未登録pathは拒否 |
| SettingOverride.value | Phase Aの空schemaをJSON型とpath別if/then制約へ具体化。未指定value/null/型/範囲/配列uniqueを区別 |
| pure交差検証 | fixed policyはcandidate必須・fallback none、autoはfixed ID null。後者はPhase A本文をschemaへ追加。speaker count矛盾・同scope重複path・file ID重複もruntime DTOで拒否 |
| optionalの内部表現 | 属性None+fields_setで欠落を扱うが、wire上でnullableを増やさない。生成schema hookで非nullable optionalのnull/defaultを除く |
| immutable containers | wireは従来のJSON array/object、内部tuple/read-only map。既存APIへの切替はない |
| empty Prompt | 構造DTOは空文字を保持。Phase Aの正規化後null規則はB2へ延期し、B1で実行設定を変えない |

JSON Schema標準で他field値との大小比較やscope内path一意性を全て記述はしない。schema-only合格でruntime DTO検証を省略しない。backend互換性・モデル実在・CUDA可否・GenAI言語制約・自由引数の安全な実行・予約引数判定はcoreが将来行う。B1が形を受理しても実行可能/安全なbackend引数だとは保証しない。

## 検証・引継ぎ

[検証記録](validation.md)を参照。LinuxでPython 3.11/3.12と実wheelを検証し、Windows VM・PyInstaller build・WebView2/installer/portableは未実施。新規API/UI/job実行を追加していないため、新機能E2E成功とは報告しない。B2では共通型→旧Configの変換、正規化/継承の正本、runtime交差制約、エラーprojection接続を別PRで実装する。B1からB2へはレビュー前に進まない。
