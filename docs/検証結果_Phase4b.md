# Phase 4b メモリ管理検証結果

## 結果

- 実行日: 2026-08-05
- 環境: Windows 11 / Core Ultra 7 255H / Intel Arc 140T / intel profile
- pyannote.audio 4.0.7 / community-1
- 認識本文、音声名、入力pathは報告・キャリブレーションへ保存していない。
- Step 0は分岐Bのため分割処理比較（Step 4-C）は対象外。

## 長時間点の推定誤差

Phase 3dと同じprocess-tree working set測定JSONのbyte値を使用した。同梱式はこれらの点からOLSで
導出したためin-sample評価であり、他環境での精度保証ではない。

| stage/device | 分 | 実測 GiB | 推定 GiB | 誤差 |
|---|---:|---:|---:|---:|
| ASR/Vulkan | 25 | 1.3687 | 1.3685 | -0.015% |
| ASR/Vulkan | 50 | 1.6188 | 1.6191 | +0.019% |
| ASR/Vulkan | 100 | 2.1204 | 2.1203 | -0.005% |
| diarization/XPU | 25 | 5.0360 | 5.0146 | -0.424% |
| diarization/XPU | 50 | 5.2002 | 5.2322 | +0.617% |
| diarization/XPU | 100 | 5.6782 | 5.6675 | -0.188% |
| diarization/CPU | 25 | 2.5639 | 2.5639 | 0.000% |
| diarization/CPU | 50 | 2.7069 | 2.7069 | 0.000% |

今回の新規3分XPU成功試行は推定4.823 GiB、実測4.962 GiBで-2.80%。R-5の3点とこの点で
ローカル式を再fitすると新規点の誤差は-1.18%となり、長時間3点も絶対誤差1.17%以内だった。
実装テストでは3点未満で既定式、3点以上でローカル式へ切り替わること、巨大外れ値を除外することを
確認した。同一長付近だけ3点では傾きを同定できず暴走することを受入試験で検出したため、
5分以上の音声長spanを追加条件として切り替えないよう是正した。

## 人工予算と実OOM

3分fixtureを使用し、ASR artifactは初回だけ生成して後続でresumeした。出力本文は確認していない。

| ケース | 条件 | 結果 |
|---|---|---|
| safe | 通常予算、device/guard auto | safe、XPU継続、peak 4.962 GiB、完走 |
| danger / auto | raw予算4.9 GiB、安全率0.2%、device/guard auto | danger、XPU→CPU事前退避、CPU peak 2.775 GiB、完走 |
| danger / explicit | raw予算4.9 GiB、安全率0.3%、XPU明示 | danger警告、XPUのままpeak 4.952 GiB、完走 |
| impossible | raw予算2.5 GiB、安全率0.4%、device auto | XPU base超過、CPUもsafeでなく、model load前にexit 3。3対処案を表示 |
| runtime OOM | guard off、XPU allocator上限2%、device auto | 実XPU OOMを捕捉、CPUへ1回だけ再試行して完走 |

OOMケースはjob logに捕捉理由、最終JSONに`trigger=oom`、`oom_retry=true`、実効device=CPUを記録した。
manifestはaudio/asr/diarization/merge/exportがすべてdoneで、既存audio/ASRが再利用された。
OOM直後のCPU working setにはXPU残留分が混ざることも実測で確認したため、その成功peakはjob metadata
には残すがCPUキャリブレーションから除外するよう是正した。

## 回帰

- `ruff check src tests tools`: pass
- `mypy`: 40 source files pass
- `pytest -m "not requires_model"`: 234 pass
- safe/danger/impossible境界、unknown、auto/明示差、OOM最大1回、保存schema、外れ値、CLI show/resetを
  model不要testで確認した。
- 実機のCPU/XPU pyannote経路とwhisper.cpp Vulkan ASR resumeを上記5ケースで確認した。
- 統合受入ハーネスP9を実行。初回は6 pass/1 failで、ほぼ同じ3分点を回帰した傾き暴走により
  P9-4が推定1940 GiBとなる不具合を検出した。音声長span 5分以上をfit条件へ追加後、P9-4を
  70.2秒で再実行してpass。ケースID別最新結果は7/7 pass。
