# B1 lockfileとprofile互換性への影響（B2開始ゲート）

調査日2026-10-30。main `1097aef`→B1 `de67f18`。

- 旧lock SHA-256: `9636904ab8d895552e64c6de18549acb2160a5045ae71037cf4a08bf198762e4`
- B1 lock SHA-256: `9445d5187a7c4a40fd99e5d719ffee4bc8f9f01f01688c373028aee67a7c38d8`
- 追加package: jsonschema, jsonschema-specifications, referencing, rpds-py（devのschema検証用）。
- 既存packageのversion変化: [('utteran', '0.1.29', '0.1.30')]。
- lock形式revisionは3→5、markerの並び等もuvの出力で変化。

`setup.ps1`のprofile manifestはlock全体のSHA256を記録し、`utteran_gui/cli.py::_profile_compatibility`は
同じ全体hashとextras/pathを比較する。したがって**mainで構築したCPU/CUDA/Intel/Vulkan全profileは、
実行依存が同じでもB1でdependency_lock_changedとなり得る**。GUIに再構築警告/ウィザード再構築が
提示される可能性があり、「runtime依存更新なし」だけで互換性維持済みとは言えない。

今回profile venvを再構築せず、既存manifestを書き換えず、互換性検査を無効化しない。
B2の製品version更新も全体hashを変えるため同じ制約を引き継ぐ。

推奨する別PRは、profileごとにOS/Python ABI・extras・実行dependency closure（version/source/hash）・
core実行互換versionを束ねるversion付きfingerprintを設計し、dev依存/format-only差分と本当のruntime差分を分離すること。
旧manifestは既知lock/closureの同等性を検証できる場合だけ移行し、証明不能を成功扱いにしない。
GUI/PowerShell両writer-readerの変更・Windows受入が必要なため、GUI改修禁止のB2には混ぜない。
旧hashの無条件受入、任意manifest書換え、依存やTLS検証の省略は対応案としない。

B2では現行の保守的判定を保持する。このprofile警告の抑制は**未解決・別PR対象**であり、既存profile互換性を改善したと報告しない。
