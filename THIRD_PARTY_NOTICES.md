# Third-party notices

utteran のソースコード自体は MIT License で提供されますが、依存ライブラリ、モデル、
および外部プログラムには個別のライセンスと利用条件が適用されます。利用・再配布前に、
実際に導入したバージョンのライセンスを確認してください。

| 対象 | ライセンス | 注意事項 |
|---|---|---|
| OpenAI Whisper | MIT | faster-whisper で互換モデルを利用します。 |
| Kotoba Whisper v2.0 faster | MIT | 日本語向け CTranslate2 モデルです。 |
| faster-whisper | MIT | CTranslate2 を利用します。 |
| OpenVINO / OpenVINO GenAI | Apache-2.0 | デバイス検出と将来 backend の準備に利用します。 |
| whisper.cpp (ggml-org) | MIT | `utteran native build` がソースから取得しビルドします。実行への利用は今後の対応です。 |
| OpenVINO whisper-large-v3-turbo-fp16-ov | MIT | OpenVINO 形式の配布モデルです。 |
| pyannote.audio | MIT | モデルのライセンスとは別です。 |
| pyannote speaker-diarization-community-1 | CC-BY-4.0 | Hugging Face 上で利用条件への同意が必要です。 |
| PyTorch / torchaudio | BSD-3-Clause | profileによりCPU版またはCUDA 12.6版wheelを導入します。 |
| NVIDIA CUDA / cuDNN / cuBLAS components | NVIDIA Software License Agreement | CUDA版PyTorch wheelに含まれる配布物の条件を確認してください。 |
| FFmpeg | LGPL-2.1-or-later / GPL-2.0-or-later | ビルド構成により異なります。utteran には同梱しません。 |
| gyan.dev FFmpeg release essentials build | GPLv3 | `setup.ps1` がユーザー領域へ取得する Windows build です。 |

この一覧は法的助言ではありません。
