# 純粋な設定継承

`preview(EditingSession, Preset, Policy)`は引数だけから結果を返す。DB、clock、pathの実在、
network、model、B4 inventoryを参照しない。参照ID/revisionが引数と不一致ならreference_unresolved。
B1 `leaf_adapters()`とSettingOverrideを利用し、親子/重複/未知path、array index/map key、
型不正、nonnullable null、schema_version overrideを拒否する。

preset.settingsのwireコピー→common leaf replace→fileごとの独立コピーとleaf replace→正規化→
ProcessingSettings strict再検証→semantic validationの順。配列/mapは全体置換。
entry削除は再計算でcommon/presetへ戻り、nullとは異なる。auto/false/[]は明示値。
formatsの[]はB1のminItems=1により422、glossary/tokensの[]は空値として扱える。
optional namespaceにleafを設定するとobjectを作るが、必要な兄弟field不足はstrict再検証でissueになる。

空Promptだけをnullへ正規化。空白だけのPromptは変更しない。languageはB2同様trimし、
case-insensitive autoを文字列autoへ正規化する。backend/modelのautoを具体値へ解決しない。
B2のempty Prompt保持とstage hashを変更しない。B5 previewを旧jobへ直接送る経路も追加しない。

全leafにpath/source/input_source/input_statusを返す。sourceはpreset/common/file/normalization、
input_sourceは正規化前の適用元、input_statusはabsent/null/empty/value（false/autoはvalue）。
値の複製はせずraw statusのみ保持する。省略optional leafもabsentとして識別する。
参照revisionはValidationResult.preset_ref/policy_refへ保持する。

cross-fieldはB1人数制約、inactive namespace（空objectも含む）、whisper.cpp VAD矛盾、
話者分離とtimestamps=never、GenAIの非スペース言語/auto/未指定言語とdiarization/alwaysを検査する。
非スペース言語集合はB4/registryとの一致テストでdriftを検出する。namespaceのoverrideは
incompatible_overrideと登録pathを返し、切替時にentryを消さない。

extra tokensにtyped field所有aliasがあればargument_conflict。残り非空tokensはargument_unverified。
B5にはnative versionに拘束した安全なoption registryがないため、allow_unverified=trueでも
安全に分類できないtokensを拒否する。Expert実行許可・tainted実行の経路は未提供。
B2/CLI/workerへtokensを転送しない。今後registryを追加する場合は別契約レビューが必要。

schema/semanticのvalidとworker_supportを分離する。GenAI/whisper.cpp、diarization、glossary、
extra args、FWのnever/VAD threshold、B2で正数必須の0値はworker_unsupported warning。
それ以外もworker_support=unverifiedでありready/実行成功を主張しない。
Prompt/glossary効果はprompt_quality_unverified。Issue #1の品質実測待ち。

pure関数は全fileのvalid/effective_settings/provenance/issues/warningsを返す。
HTTPは不正なfileがあれば422 Errorへ全issueを`/files/<index>/<field>`で投影し、成功時だけ
ValidationResultを返す。client_file_id対応は送信filesの同indexから得る。jobは一件も作らない。
