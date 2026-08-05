# Phase 4b pyannote 4.x 分割可否調査

## 判定

**分岐 B** と判定する。pyannote.audio 4.0.7 の部品を個別に呼ぶことはできるが、
community-1 と同等の結果を保ったままチャンク処理する公開 API はなく、非公開属性と
`SpeakerDiarization.apply` の実装詳細への強い依存が必要になる。

この判定により Phase 4b では分割処理（Step 3）を実装しない。メモリ予算、推定、
キャリブレーション、事前警告、自動 CPU 退避、OOM 再試行を実装し、分割処理は
利用者が保守コストを了承して別途決定するまで保留する。

## コード調査

導入済み pyannote.audio 4.0.7 の
`pyannote.audio.pipelines.speaker_diarization.SpeakerDiarization` を確認した。

- segmentation は `get_segmentations(file)` で `SlidingWindowFeature` として取得できる。
- speaker embedding は `get_embeddings(file, binary_segmentations)` で
  `(num_chunks, local_num_speakers, dimension)` の配列として取得できる。
- `pipeline.clustering(...)` は外部から embeddings と binary segmentations を受け取れる。
- 通常／exclusive diarization は、同じ hard cluster を `reconstruct` に渡し、後者だけ
  frame speaker count を1に制限して再構成される。
- したがって「全 embedding を保持し、全体で1回だけ cluster する」という計算自体は可能。

一方、これをチャンク音声へ適用するには、公開された pipeline 呼出しだけでは足りない。

- `apply` 内の binarize、speaker count、inactive speaker 処理、cluster 数補正、label mapping、
  centroid 並べ替え、通常／exclusive 再構成を外側で複製する必要がある。
- receptive field は `pipeline._segmentation.model.receptive_field`、embedding dimension 等は
  `pipeline._embedding` という非公開属性に依存する。
- `SlidingWindowFeature` は単一の等間隔時刻格子を前提とする。重複チャンクの局所時刻を
  全体時刻へ移し、重複窓を除去・統合してから `speaker_count` と `reconstruct` に渡す処理は
  pyannote から提供されない。
- チャンク境界では segmentation model の文脈が変わるため、同じ窓でも一括処理と値が一致する
  保証がない。重複領域の採用規則まで utteran 側で固定する必要がある。

`get_segmentations`、`get_embeddings`、`reconstruct` は先頭 underscore こそないが、
トップレベル Pipeline の互換 API として保証された分割契約ではない。必要な処理の一部が
明確な非公開属性に依存する以上、本指示の「公開 API または安定した内部構造」には該当しない。

## 合成音声による確認

実データを使わず、固定 seed の合成12秒波形を CPU で処理した。segmentation の実出力を取得し、
構造確認のため1話者が常時 active の mask を与えて後段を実行した。

| 中間値 | 実測 shape |
|---|---|
| segmentation | `(3, 589, 3)` |
| embedding | `(3, 3, 256)` |
| hard cluster | `(3, 3)` |
| centroid | `(1, 256)` |
| 通常再構成 | `(712, 1)` |
| exclusive 再構成 | `(712, 1)` |

これにより中間部品が実際に呼べることと、exclusive を clustering 後に再構成できることは確認した。
合成波形の内容、ファイル名、パスは保存していない。

## 自前実装の見積もりとリスク

pyannote の現行 `apply` を基礎に segmentation → embedding → global clustering → reconstruction
を組む場合、製品コード 800〜1,200 行、model 不要の構造テストと実 model 比較 500〜800 行、
実機検証・resume artifact・移行文書を含めて概ね 2〜4 週間を見込む。これは精度調整を除く。

破損リスクは高い。特に model specification、powerset の表現、private inference/model 属性、
clustering の引数と返却 shape、speaker counting、label/centroid mapping が更新点になる。
pyannote の minor 更新ごとに一括／分割比較を再実施し、対応バージョンを限定する必要がある。

## 分割なしで救える範囲

Phase 3d の単一 Intel 環境では XPU の基礎量が約5.3 GB、CPU は約2.6 GBである。
XPU の共有メモリ予算に収まらないが CPU の空き RAM には収まる環境は、自動 CPU 退避で救える。
8 GiB CUDA のように専用 VRAM が危険でも、システム RAM に CPU 基礎量と長さ係数分の余裕が
あれば同様である。CPU 基礎量すら入らない環境は分割でも救えず、話者分離省略または音声を
利用者が個別ファイルへ分ける必要がある。性能値は単一環境の少数点であり保証値ではない。
