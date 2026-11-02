# 既存互換性・後続境界

B4は未接続の追加package/module。devices/native/profiles/registry/config_adapter/application/
worker/worker_runtime、既存EnvironmentService/derive_options、GUI API/画面の実装を編集していない。
既存ジョブ、モデル、profile、manifest、cacheを変更・移行しない。B1/B3 schema bytesも維持する。

| 既存の読取情報 | 再利用できる情報 | B4で追加証拠が必要な点 |
|---|---|---|
| devices --json | library/device/status、timeout/error、既存cache fingerprint設計 | cacheはprofile非依存、原観測時刻やmodel smokeなし。コマンドはcache/logも書くため直接再実行しない |
| models list --available --all --json | backend/model/catalog、installed/files/path | revision/完全worker file条件/実smokeなし。B4標準collectorはmanaged pathだけ別途読取 |
| native status --json | pinned manifest、executable存在 | runtime/model別IR/smokeなし。B4はNativeBuilder.statusのread-only部分だけ利用 |
| profiles list --json | layout/extras/存在 | generation/lock互換性/smokeなし。標準collectorは現sys.prefixだけ |
| GUI profile manifest | schema、lock/extras/path互換性 | generationと依存/driver/model証拠は別。profile情報を他profileへ転用しない |
| hardware OS観測 | profile構築前の軽量検出 | 推論runtime成功とは別。通常起動へ新probeを追加しない |

独立bridgeを使う理由は旧CLIのlogging初期化とdevicesのcache/diagnostic書込みを回避するため。
旧CLIの引数/JSON/終了codeを変更しない。GUI側に対応matrixやrecipe判定を複製しない。
B4 GUI adapterはcore/heavy依存をimportしない。旧profileにmoduleがない場合はunknownとして扱い、
fallbackで旧CLIを実行したり勝手にuv syncしない。

日常版管理規約（要件定義24.1、release手順）に従い0.1.32→0.1.33。
uv.lockのroot versionだけ追従し、依存package版の変更はない。
**lock全体hash問題は残る**。versionだけの変更でも既存manifestはdependency_lock_changedになり得る。
B4はfresh failedとしてblockedへ表現し、hash検査skip/manifest修正/venv再構築で隠さない。
依存closure fingerprint/移行設計は別PR。現行compatibility条件を黙って変えない。

既存Linux pytest 3失敗、mypy Windows ctypes 9件、Windows汎用PowerShell起動検査の
verify_uninstaller/Inno Setup前提は別PR対象。B4にskip/静的検査無効化は追加しない。
CIの変更はB4 schema checkと既存Python3.11/3.12 matrixへのB4テスト追加だけ。

B5: preset/policy/継承API。B6/B7: durable store、revision再確認、job snapshot、queue、attempt/fallback。
B8: setup承認/構築/再検証。B9/U1: API/SSEとGUI反映。
実GPU/native、driver検出provider、性能/quality、Windows/PyInstaller、worker追加backendの安全性は別受入。
