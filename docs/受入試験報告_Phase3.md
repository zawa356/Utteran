# Phase 3 受入試験報告

## 概要

- 実施日時: 2026-08-04 14:40 JST〜（実施中）
- 開始時コミット: `f96b00df728529e609ed1a8eb5d96a858fc18c3f`
- ブランチ: `test/acceptance-phase3`
- 実行環境: Windows build 26200、Python 3.14.6（システム）、uv 0.12.1
- CPU: Intel Core Ultra 7 255H、16 logical cores、物理メモリ 68,161,626,112 bytes
- GPU: Intel Arc 140T、driver 32.0.101.8247、PyTorch XPU表示 36,049,137,664 bytes（共有メモリ）
- 開始時ディスク空き容量: 246,423,302,144 bytes
- ユーザープロファイルパス: 非ASCII文字を含む
- 試験対象: `cpu`、`intel`、`vulkan` プロファイル
- 対象外: `cuda` プロファイル（NVIDIA GPU不在のため作成せず、利用不能時の案内のみ確認する）
- 入力実ファイル: 96,253,388 bytes、1,486.000秒、MP4/H.264 1920x1080 60fps/AAC 48kHz stereo
- 仕様書との差異: 本機にある実ファイルは約24分46秒で、仕様書が想定する約2時間20分ではない。
- 総合結果: 実施中

実入力の原本は変更・削除・移動せず、文字起こし本文、固有名、秘密値を本報告やGit履歴へ
記録しない。成果物と試験用クリップはGit ignore対象の `output/` 内に保持する。

## 開始時状態

- 作成済みプロファイル: `cpu`（1.0 GiB）、`intel`（5.3 GiB）
- 未作成プロファイル: `cuda`、`vulkan`
- Intel環境のauto選択: ASR=`whisper-cpp/openvino_vulkan`、話者分離=`pyannote/xpu:0`
- ネイティブビルド: whisper.cpp v1.9.1、commit
  `f049fff95a089aa9969deb009cdd4892b3e74916`、4構成すべて実行可能
- 導入済みモデル: faster-whisper large-v3-turbo、pyannote community-1、
  whisper.cpp large-v3-turbo-q5_0

## 試験結果サマリー

| グループ | 実施 | 合格 | 失敗 | 未実施 | 保留 |
|---|---:|---:|---:|---:|---:|
| P0 疎通確認 | 1 | 1 | 0 | 0 | 0 |
| P1 プロファイル管理 | 9 | 9 | 0 | 0 | 0 |
| P2 実行手段 | 6 | 6 | 0 | 0 | 0 |
| P3 ネイティブビルド | 8 | 8 | 0 | 0 | 0 |
| P4 モデルカタログ | 9 | 9 | 0 | 0 | 0 |
| P5 OpenVINO IR変換 | 9 | 9 | 0 | 0 | 0 |
| P6 whisper.cpp ASR | 9 | 9 | 0 | 0 | 0 |
| P7 単語レベルタイムスタンプ | 8 | 8 | 0 | 0 | 0 |
| P8 auto選択とフォールバック | 7 | 7 | 0 | 0 | 0 |
| P9 XPU話者分離 | 9 | 9 | 0 | 0 | 0 |
| **暫定合計** | **75** | **75** | **0** | **0** | **0** |

### P0 疎通確認

- Intelプロファイル、whisper.cpp/openvino_vulkan、話者分離なしで30秒クリップを処理した。
- exit 0、SRT/VTT/JSON/TXT/Markdownの5形式を生成した。
- JSONはschema version 1、7セグメント、空セグメント比0、日本語文字比1.0、最大連続重複1。
- セグメント時刻は単調で、`start < end`かつ記録された有効音声長21.68秒以内だった。
- ASR 10.705秒、実行フェーズ合計10.803秒。

### P1 プロファイル管理

- `setup.ps1 -List`、CPU/Intelの更新、Vulkanの新規作成が成功した。CUDAは作成していない。
- 更新前後のtorchはCPU=`2.11.0+cpu`、Intel=`2.11.0+xpu`で不変。Vulkanは`2.11.0+cpu`。
- 既定profileをCPUへ変更して確認後、Intelへ戻した。
- Vulkan削除時に対象と1.1 GiBの解放見込みが表示され、確認処理を監査した。
  実削除後にVulkanを再作成し、正常probeまで復元した。
- 旧 `.venv` は変更せず、setupの移行案内を確認した。
- 最終venvサイズ: CPU 1.0 GiB、Intel 5.3 GiB、Vulkan 1.1 GiB。

### P2 実行手段

- 既定profileと `-Profile intel` 明示の両方で処理がexit 0となり、起動時にIntel表示を確認した。
- 未作成のCUDA profile指定はexit 1となり、`setup.ps1 -Profile cuda` の案内を表示した。
- 異常入力のexit 4が `run.ps1` から透過された。
- `profiles list/current/path` はすべて成功し、currentはIntel、pathは実在venv rootだった。

### P3 ネイティブビルド

- Intel/Vulkan両profileのstatusで共有された4構成すべてが実行可能だった。
- manifestはschema 1、whisper.cpp v1.9.1、固定commit一致、profile固有OpenVINO絶対パスなし。
- 通常再実行、CPU限定force build、CPU clean後の再ビルドが成功した。
- CPU限定操作後も未指定のOpenVINO/Vulkan/OpenVINO+Vulkan 3構成をmanifestに保持し、
  最終statusで全4構成が実行可能だった。

### P4 モデルカタログ

- `list`、`list --available`、`--all`、`list --json` の表示・構造を確認した。
- 既定一覧に英語専用 `.en` はなく、`--all` のみで表示された。量子化違いも登録済みだった。
- whisper.cpp base（141.1 MiB）を取得→verify→削除→再取得→verifyし、最終的に正常状態で保持した。
- 曖昧IDは候補付きexit 2、未登録IDはexit 2、非対話ID省略はexit 1で拒否された。
- `models path` は非ASCII文字を含む実在モデル保存先を表示した。

### P5 OpenVINO IR変換

- 既存large-v3-turbo IRを保持したまま、base IRをGPUで2回生成し、各回exit 0だった。
- 非対話・`--yes`なしでは追加重み取得を告知して拒否し、CPU profileではIntel/setup案内付きexit 3だった。
- 初回は`--purge-cache`付きで実行し、非ASCII記号を含む変換出力でもcp932例外は再発しなかった。
- baseとbase-q5_1の両モデルで、同一IRへの規約名hardlink（XML/BIN）とverify成功を確認した。
- base IRをremoveし、一覧で未生成となることを確認後に再生成した。large系IRは削除していない。

### P6 whisper.cpp ASR（4構成）

| 構成 | ASR秒 | セグメント | 単語 | 文字数 | 話者数 |
|---|---:|---:|---:|---:|---:|
| CPU | 25.099 | 7 | 0 | 95 | 0 |
| OpenVINO | 11.958 | 7 | 0 | 89 | 0 |
| Vulkan | 3.122 | 7 | 0 | 96 | 0 |
| OpenVINO+Vulkan | 11.018 | 7 | 0 | 101 | 0 |

- 同一30秒クリップ、話者分離なしで4構成ともexit 0。非ASCIIモデル保存先から正常実行した。
- CPU基準でセグメント差0%、文字数差-6.32%〜+6.32%、話者数完全一致。単語TSなしのため単語数は全0。
- q5_0モデルとOpenVINO IRの組み合わせ、未知構成のexit 3を確認した。
- CPU構成をcleanした状態の明示指定はexit 3となり、修正後は対象`native build --variant`を案内する。

### P7 単語レベルタイムスタンプ

| 条件 | セグメント | 単語 | セグメント外単語 | 文字化け |
|---|---:|---:|---:|---:|
| auto・話者分離なし | 7 | 0 | 0 | なし |
| always・話者分離なし | 7 | 88 | 0 | なし |
| never・話者分離なし | 7 | 0 | 0 | なし |
| auto・XPU話者分離あり | 5 | 88 | 0 | なし |
| faster-whisper・話者分離なし | 6 | 55 | 0 | なし |

- `auto`/`always`/`never`は仕様どおり単語取得を切り替え、全単語時刻が所属segment内だった。
- DTW全`-1`時の単語破棄と警告経路はモデル不要回帰試験を再実行し合格した。
- 同じ短時間素材ではwhisper.cppがfaster-whisperより単語数60%多かった。Phase 3bの18%より大きいが、
  日本語の分割粒度と短い標本の影響があるため、この値だけで閾値変更は行わずP10の3分結合結果で判断する。

### P8 auto選択とフォールバック

- devices JSONはASR=`whisper-cpp/openvino_vulkan`、話者分離=`pyannote/xpu:0`と理由2件を返した。
- device/backendを指定せず、導入済みq5_0モデルだけ明示した実処理がexit 0で完走した。
  ASR 20.163秒、話者分離19.093秒、実行フェーズ合計39.399秒。
- GPU初期化失敗時にovvk→Vulkan→OpenVINOの順で1回だけ進む分岐と、明示指定時に進まない分岐を
  executable付きのモデル不要試験として追加し合格した。
- NVIDIA不在でfaster-whisper/CUDA明示はexit 3となり、CUDA利用不能と明示指定では
  フォールバックしない旨を表示した。

### P9 XPU話者分離

- 6候補窓を音声活動量で選び、4追加候補をXPU推論した。内部5話者を検出した候補を
  `clip_03m_multi.mp4` として採用した（内容は記録していない）。
- 同じ3分素材のraw diarizationはCPU/XPUとも内部4話者、exclusive 56区間で、
  話者label列も全境界も完全一致（最大差0秒）した。
- XPU同一条件2回は出力話者行5（UNKNOWN込み）、11セグメント、時刻・話者列が完全一致した。
- `--num-speakers 3`はraw diarizationで3話者・3種類labelとなった（最終出力はUNKNOWN込み4種類）。
- CPU profileからXPU明示はexit 3でIntel profile不整合と自動fallbackなしを説明した。
- XPU OOMの共有system RAM案内は人工例外のモデル不要試験に合格した。
- ASR=OpenVINO+Vulkan／話者分離=autoの実処理と、従来`--device cpu`のCPU両backend実処理が完走した。
- 3分話者分離実測はCPU 106.681秒、XPU 41.953秒（再利用ASRを除く）。

## 発見した不具合と対応

| ID | 内容 | 深刻度 | 対応 | コミット |
|---|---|---|---|---|
| P3-U-001 | `setup.ps1` のprofile検証・既定profile設定で日本語出力や非ASCIIパスが誤デコードされる | 高 | 子processをUTF-8固定し、環境変数を復元。実機再試験とモデル不要回帰試験に合格 | 失敗 `1a0c98e`／修正 `a5be140` |
| P3-U-002 | native manifestの`cmake_flags`にprofile固有の絶対`OpenVINO_DIR`が保存される | 中 | 移植可能なplaceholderへ置換し、比較時も正規化 | 失敗 `96d74ff`／修正 `3be1a19` |
| P3-U-003 | `native build --variant cpu --force`がmanifestをCPUだけで上書きし、未指定3構成を未試行扱いにする | 高 | 未指定backend/error entryを保持し、回帰試験追加 | 失敗 `8ab7c8a`／修正 `3be1a19` |
| P3-U-004 | 未ビルドwhisper.cpp構成の指定時に`native build`の復旧案内が表示されない | 中 | 構成名入りの復旧コマンドを追加 | 失敗 `e95fc95`／修正 `4e5967c` |

## 未修正のまま残した事項

- 現時点ではなし。

## 性能測定結果

P13、P14完了後に記載する。

## 構成間の一致度

P6完了後に記載する。

## CPU / XPU の話者分離一致

| 指標 | CPU | XPU | 差 |
|---|---:|---:|---:|
| 内部話者数 | 4 | 4 | 0 |
| exclusive区間数 | 56 | 56 | 0 |
| 最大境界差 | - | - | 0秒 |
| 平均境界差 | - | - | 0秒 |

複数話者音声でも仕様の一致基準を完全に満たした。

## 結合検証の結果

P10完了後に記載する。

## 未実施・保留の試験と理由

- CUDA関連: NVIDIAハードウェア不在のため、プロファイル作成と実推論は実施しない。

## 利用者への確認事項

- 現時点ではなし。

## 品質確認用の出力ファイル

- P0: `output/_acceptance_p3/p0/clip_30s.{srt,vtt,json,txt,md}`
