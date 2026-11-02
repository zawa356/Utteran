# B3互換性・配布

## B2・CLIとの関係

B2のdiarization_device問題を独立commit `294c383` で修正した。
話者分離有効時は空/空白のみ/前後空白/大文字小文字autoを拒否し、明示cpu/cuda:0/xpu:0を保持。
無効時の既存動作は維持。16追加回帰ケースとB2関連104件で確認後にB3を分岐した。

workerはDTO → nested Config field検査 → adapt_settings → application.prepare_request →
offline_preflight → application.preflight_dependencies → application.execute_singleの順で接続する。
Config.load、Typer、CLI model承認関数を呼ばない。
Config.model_validateのBaseSettings環境sourceも監査し、worker専用processの設定section envを
mainで除去してから変換する。省略値や不正env JSONが確定要求へ再適用されることを防ぐ。通常CLI/GUIのsource、Pipeline/JobStore/ASR/
export algorithm、core schemaとstage hash/policy versionは変更しない。

合成ASRを使った実Pipelineで、worker内service呼出し、5stage進捗、既存job/resume、force/fresh、
private Promptのbackend到達、既存成果物とhash整合を確認する。CLIのB2 goldenを全回帰で維持する。
workerはCLIより対応範囲が狭く、whisper.cpp/pyannote/GenAI/glossary/実験引数/batch等を
黙って無視しない。未対応なら明示失敗し、CLIへ自動fallbackしない。

## profile・lock

既存CliAdapter.profile_infoのlock全体SHA-256/extras/venv_path比較を再利用し、compatible=Trueだけを
起動する。module未導入はhello前の失敗として検出する。hello前には実行要求を送らない。
古いwheelにworkerがあるとは仮定しない。同じ製品versionだけでもworker能力を判定できない。
依存versionは変更せず、規約に従い製品versionを0.1.32へ上げ、lockのroot versionだけ追従する。
lock全体hashは変わるため旧profileがblockされ得るが、検査を回避しない。protocolは独立管理する。
新moduleを含むwheelの導入は明示的な開発環境準備が必要で、自動導入はしない。

既存profileを試験の都合で書き換えたり、venvを再構築したりしていない。
試験用manifest/venvはpytestの新規temporary directoryにだけ作成する。
lock全体hashの課題が既存profileをblockする場合の独立計画:

1. GUI/PowerShell/setup双方のfingerprint生成箇所を列挙する。
2. runtime closure/extras/ABI/Python/build対象だけのversion付きfingerprintを設計する。
3. 旧manifestをread-onlyで評価し、unknownとincompatibleを分ける移行試験を追加する。
4. 明示的verify/移行後のmanifest更新を別PRで扱う。B3では検査回避しない。

## Windows・配布

Windows path/flags/Job Objectは既存process helperを使う。POSIXではPython symlinkをresolveして
system interpreterへ切り替わらないようにした。新クライアントをGUI初期化へ登録していない。
GUIへ推論依存を追加せず、contractsとGUI clientがcore/heavy moduleなしでimportできることを検査する。
既存wheel対象4packageに新moduleが自動で含まれることをwheel内容でも確認する。

Linuxでのvenv/IPC/process group試験と、Windowsでの実受入は別である。
Windows CreateProcess/console非表示、Job Object親強制終了、PowerShell非経由起動、WebView2、
pywebview、PyInstaller onedir、portable、profile wheelの実配置・再導入はWindows VM未検証。
既存のWindows-only testもLinuxではskip。対応済み・配布合格とは報告しない。
