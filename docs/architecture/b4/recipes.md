# Recipeとtransport

`utteran.capabilities.recipes.RECIPES`だけがsoftware supportの正本。
collectorはrecipeのbackendと同backendのmodel catalogをjoinする。汎用backend×native×deviceの
直積は作らず、未知recipe IDは拒否する。device/compute/OS/model familyを検証してから判定する。

| ID | backend / model family | native variant | encoder要求 | decoder要求 | 根拠 | B3 worker |
|---|---|---|---|---|---|---|
| fw-cpu | faster-whisper / CTranslate2 | null | CPU | CPU | asr/faster_whisper、devices | 条件付き |
| fw-cuda | faster-whisper / CTranslate2 | null | CUDA | CUDA | 同上 | 条件付き |
| wc-cpu | whisper-cpp / GGML | cpu | CPU | CPU | asr/whisper_cpp、native | 非対応 |
| wc-vulkan | whisper-cpp / GGML | vulkan | backend-default | Vulkan | 同上 | 非対応 |
| wc-ov | whisper-cpp / GGML | openvino | OpenVINO GPU | backend-default | 同上、モデル別encoder IRが必要 | 非対応 |
| wc-ov-vk | whisper-cpp / GGML | openvino_vulkan | OpenVINO GPU | GGML Vulkan | 同上、モデル別encoder IRが必要 | 非対応 |
| genai-ov | openvino-genai / GenAI IR | null | 選択OV device | 選択OV device | asr/openvino_genai、models/catalog | 非対応 |

OpenVINO+CUDA recipeは存在しない。OV+VulkanはOpenVINO自体のVulkan実行を意味しない。
要求backend-default/selected-deviceを成功した実routeとしてコピーしない。実routeはsmoke evidence側。
GenAIの対応deviceは現実装のCPU/GPU/NPU。NPU推奨や性能優位を意味しない。
対象OSは既存profile構造のWindows/Linux。FWのCPU computeはint8/int8_float32/float32、
CUDAは上記とint8_float16/float16。whisper.cppはggml、現GenAIモデルはint8。
具体的なcomputeのruntime利用可能性は別Observationで要求する。

標準collectorは各recipeの先頭device/compute、index 0だけを**要求値**として列挙する。
GenAIはCPUのみ。追加のGPU/NPU/device index/precisionには明示providerのdeploymentが必要。
新たなsmoke実行や自動hardware probingは行わない。

B3 workerにはさらにlocal完全FWモデル、明示device/compute、話者分離無効、input_count=1が必要。
local_complete_modelはmodel.bin/config.json/tokenizer.json/preprocessor_config.jsonの存在条件。
これだけでreadyにはならず、workerを含むfingerprintとsmokeも必要。CLIのsmokeはworkerへ転用不可。
B2 GenAI structured adapterは未対応のまま。B4のCLI対応は既存core/CLIのrecipe対応を示し、
B2/workerへの変換実装が完成したという意味ではない。

pyannoteはASR routeから独立したcomponent。CPU/CUDA/XPU、device index、model、
component fingerprintと個別Observationを要求する。XPUは現Utteranの検証範囲でWindowsのみ。
GenAI＋話者分離はauto/ja/zh/th/lo/my/yueを拒否する。
実装されないglossary/extra arguments等は拒否、固定backend/model/compute、inactive namespace、
word timestamp等の条件も評価する。対応していない構成をlibrary一般論で追加しない。
