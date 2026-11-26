# 束G マルチプロファイル実行（0.1.72、開発版）

対象は `--next-ui-v1`。B8 でモデル・実行構成を確認して承認し、B4 smoke が同じ設定で短い合成音声を処理する。B7 は投入と起動直前に同じ証拠を照合する。初回の Preset/Policy 作成は束Fの一括 create-if-empty を使う。既定 GUI と従来の CLI は従来経路を使う。

| 優先 | プロファイル | ASR | 話者分離 |
|---|---|---|---|
| 1 | Windows Intel | whisper.cpp Vulkan（encoder/decoder） | XPU／CPU、または無効 |
| 2 | Windows Intel | OpenVINO GPU encoder ＋ Vulkan decoder、OpenVINO GPU encoder ＋ CPU decoder | XPU／CPU、または無効 |
| 3 | Windows Vulkan／CPU | Vulkan（encoder/decoder）／faster-whisper CPU | CPU、または無効 |
| 4 | CUDA | faster-whisper CUDA | CUDA／CPU、または無効 |

Intel 推奨は OS の物理検出から始め、Vulkan・OpenVINO・XPU の runtime 検出と、B4 のモデル実行を別に示す。Intel、Vulkan の B4 は Windows の PCI ディスプレイアダプターを一意に識別できる構成が対象。複数 GPU、Linux XPU、AMD 話者分離、OpenVINO GenAI は後続。OpenVINO と XPU では OS と runtime の名前を照合できない機器も保留になる。

## 実行と証拠

- ASR、話者分離の既存アダプターを使用する。B3 は ASR モデルを解放してから話者分離用のメモリを確認し、pyannote をロードする。core の中間結果と再開、話者付きエクスポートをそのまま使う。
- whisper.cpp は固定 upstream に既存 VAD／DTW patch と専用の stdin 引数処理を適用する。子の argv は実行ファイルと固定フラグだけ。音声、VAD、結果には private 作業ディレクトリ内の固定パスを使う。プロンプトはメモリ上のパイプで渡す。C++ の推論成功後、GGML の graph 出力と行列積を担当した backend、および OpenVINO の実行デバイスを照合する。Whisper の軽量な CPU 補助演算は許容する。従来の manifest は CLI 用に引き続き読める。B3 では variant ごとにパッチ版を確認する。
- pyannote community-1 の対象は Hugging Face revision `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`、SDK は lock の `4.0.7`。config の Git blob ID と4部品の SHA-256 を固定する。ハッシュ確認済みバイト列を private ディレクトリにステージし、ローカルの `Model` 2個と `PLDA` を構築して `SpeakerDiarization` に渡す。外部識別子、pipeline の動的クラス／preprocessor、既定 HF cache はロードに使わない。torch の両モデルの parameter/buffer device を照合する。HF token と利用同意は未取得資産を承認済み Setup で取得する場合に使用する。B3/B4 の環境には token を渡さない。
- smoke scope v2 には profile/lock/runtime、OS/driver とデバイス、モデルと native 一式のファイル識別、話者分離資産、完全な設定を含める。旧 scope の証拠では実行できない。VAD と必要な単語時刻を含め、レビュー済みの合成日本語音声で実行する。成功した実経路と設定が一致した場合に限り、メモリだけが取得不能なら `memory_unknown` の警告付きで実行できる。計測できる CPU/NVML 空き容量は refresh・prestart でも比較する。既知の不足、route/identity 不一致、期限切れや必須証拠不足は拒否する。短尺計測はピークの下限で、代表長尺で確認する。OOM は固定エラーで返す。
- NativeBuilder の variant ごとの manifest、実行パス解決、現在の local build を流用する。承認済み B8 が MSVC Build Tools／Vulkan SDK／VC++ runtime／Intel driver 等の既存前提に従ってビルドし、必要な場合だけ IR を準備する。一般利用向けの署名・検証済み CI 成果物の取得は [#8](https://github.com/zawa356/Utteran/issues/8)・[#7](https://github.com/zawa356/Utteran/issues/7) で扱う。whisper.cpp は MIT、community-1 は CC-BY-4.0 と gated 利用条件が適用される。

## 総合受入（[#22](https://github.com/zawa356/Utteran/issues/22)）

1. Windows VM で専用マーカー付きの新規 root を用意する。旧 root の設定・モデル・成果物の検証前後の状態を比較する。既定 GUI と CLI の起動、VC++／driver／toolchain 不足の表示を確認する。#88 の VM 固有の競合・ファイルシステム試験も実施する。
2. Windows Intel 単一 PCI GPU 実機で cold 検出と強制 refresh を行う。承認済み Setup で Vulkan＋XPU、ASR 単独、OpenVINO＋Vulkan、OpenVINO の順に、ロード・推論・実デバイス・単語時刻を確認する。OpenVINO は encoder と decoder を個別に記録する。Vulkan profile＋CPU 話者分離、CPU profile＋CPU 話者分離を続ける。
3. ダウンロード完了後にネットワークを切りトークン無しで再検証と B7 実行を行う。合成または許諾済みの短尺複数話者音声と代表長尺を使用し、話者数設定、重複話者と単語時刻、結果ラベル、SRT/VTT/TXT/MD/JSON、既存の再出力導線を確認する。取消・再開で旧成果物や設定が変化しないことを確認する。
4. メモリ情報を取得できない経路で警告付き実行、既知不足で拒否、実 OOM の固定エラーと手動再試行を確認する。driver・モデル・variant を変更して旧証拠の無効化、複数 GPU・異なるデバイス・CPU fallback の拒否、Setup と未終了子 lease 中の重い診断保留を確認する。NVIDIA が利用可能なら CUDA 単独、CUDA＋CUDA/CPU 話者分離、#89 の cold/refresh を追加する。
5. Windows・GPU・実モデル・配布 runtime は本開発の実機受入では **NOT_RUN**。CI の合成結果と区別して記録する。Windows の再出力は #88 の統合・受入後に確認する。

PR #91 再レビューの残件は、診断が gate 取得後に残留 lease を再確認する修正と、NVML 標本スレッドが生存中の並行 shutdown を防ぐ修正を取り込んだ。初回 defaults の一覧ページ境界の改善は後続。
