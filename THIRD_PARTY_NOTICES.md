# Third-party notices

utteran のソースコード自体は MIT License で提供されますが、依存ライブラリ、モデル、
および外部プログラムには個別のライセンスと利用条件が適用されます。利用・再配布前に、
実際に導入したバージョンのライセンスを確認してください。

**モデルはcodeと別の配布物です。** `utteran models`が表示するlicense、配布元のmodel card、
gated条件をdownload前に確認してください。利用条件への同意を要求されるmodelは、同意なしに
取得・利用できません。gatedでないmodelも、利用・再配布時点の条件確認が必要です。

| 対象 | ライセンス | 注意事項 |
|---|---|---|
| OpenAI Whisper | MIT | faster-whisper で互換モデルを利用します。 |
| Kotoba Whisper v2.0 faster | MIT | 日本語向け CTranslate2 モデルです。 |
| faster-whisper | MIT | CTranslate2 を利用します。 |
| CTranslate2 | MIT | CPU/CUDA向けWhisper推論runtimeです。 |
| Hugging Face Hub client | Apache-2.0 | model metadataと明示downloadに利用します。 |
| keyring | MIT | OS credential storeからtokenを取得します。 |
| platformdirs | MIT | user config/cache/data pathを解決します。 |
| Pydantic / pydantic-settings | MIT | config validationと設定source統合に利用します。 |
| python-dotenv | BSD-3-Clause | `.env`のlocal読取りに利用します。 |
| Rich | MIT | console表示に利用します。 |
| Typer | MIT | CLIに利用します。 |
| OpenVINO / OpenVINO GenAI | Apache-2.0 | デバイス検出と将来 backend の準備に利用します。 |
| OpenAI Whisper Python package | MIT | OpenVINO encoder IR生成時に利用します。 |
| ONNX Script | Apache-2.0 | OpenVINO encoder export時に利用します。 |
| ONNX Runtime | MIT | 将来backend用のoptional runtimeです。 |
| CMake | BSD-3-Clause | whisper.cpp native build用のoptional toolです。 |
| whisper.cpp (ggml-org) | MIT | `utteran native build` がソースから取得・ビルドし、ASRに利用します。 |
| whisper.cpp GGML Whisper models | MIT（変換元OpenAI Whisper） | repositoryへ同梱せず、選択fileだけを明示取得します。model cardも確認してください。 |
| Silero VAD v6.2.0 / whisper.cpp GGML conversion | MIT | opt-in VAD model。repositoryへ同梱しません。 |
| OpenVINO whisper-large-v3-turbo-fp16-ov | MIT | OpenVINO 形式の配布モデルです。 |
| pyannote.audio | MIT | モデルのライセンスとは別です。 |
| pyannote speaker-diarization-community-1 | CC-BY-4.0 | Hugging Face 上で利用条件への同意が必要です。 |
| PyTorch / torchaudio | BSD-3-Clause | profileによりCPU版またはCUDA 12.6版wheelを導入します。 |
| NVIDIA CUDA / cuDNN / cuBLAS components | NVIDIA Software License Agreement | CUDA版PyTorch wheelに含まれる配布物の条件を確認してください。 |
| Vulkan Loader / headers / SDK components | 主にApache-2.0 | 利用者が別途導入します。driverはvendor固有条件です。 |
| shaderc / `glslc` | Apache-2.0 | Vulkan native build時にVulkan SDKから利用します。同梱しません。 |
| FFmpeg | LGPL-2.1-or-later / GPL-2.0-or-later | ビルド構成により異なります。utteran には同梱しません。 |
| gyan.dev FFmpeg release essentials build | GPLv3 | `setup.ps1` がユーザー領域へ取得する Windows build です。 |

Pythonのtransitive dependenciesにも個別licenseがあります。release時は`uv.lock`の解決結果と
各配布packageのlicense metadataを再確認してください。この一覧は法的助言ではありません。
