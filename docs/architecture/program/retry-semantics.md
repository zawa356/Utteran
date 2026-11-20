# retryの意味 — Design FreezeとB7実装の差異と確定方針

更新: 2026-11-16 JST。状態: **owner確定（下記）**。B7の契約・DBは変更しない。

## 0. owner確定（2026-11-16）

1. **案A**: B7の既存retry契約（同じsnapshotを持つ新しいJob、`retry_of`）を維持する。UIは`retry_of`の連鎖を1つの論理ジョブとして扱う（束C2b）。
2. **出力先はA-1**: retryごとに別ディレクトリ（束C1の現状どおり）。
3. **markerだけのディレクトリは自動削除しない。**
4. UI-3-0 Design Freezeに「内部Job ID」と「論理ジョブ」の区別を追補する（[design-freeze §7](../ui3/design-freeze.md)に追補済み）。

以下は判断時の整理（経緯として残す）。

## 1. 事実

| 出典 | retryの定義 |
|---|---|
| UI-3-0 Design Freeze（[design-freeze §1](../ui3/design-freeze.md)「Expertの再投入」、[iteration-5 §4.3](../ui3/iteration-5.md)） | 「`retry`＝既存snapshotの**同一Job**」。表では Job ID「同じ」、snapshot「元のまま」 |
| B7実装（PR #32でmain統合、`JobControlStore.retry_job`、[b7/job-snapshot](../b7/job-snapshot.md)） | 「New job, identical snapshot body, `retry_of` link. The source is untouched.」＝**同じsnapshot bodyを持つ新しいJob**（新Job ID、`retry_of`で元を指す）。元のJobは`failed`/`cancelled`/`interrupted`のまま残る |
| 2026-11-16指示書 §6 | 「`retry`＝同一Snapshotの継続、`rerun`＝現在設定から別Job ID」 |

- 差異は**束C1以前からある**（Design Freezeは2026-11-06の合成プロトタイプ、B7は2026-11-02〜03の実装）。束C1が作った差異ではない。
- 新UIはまだretryを呼んでいない（`frontend/src/api/v1Resources.ts`に「retry/rerun are later stages」）。画面への接続は束C2。
- 一致している点: snapshotは不変で同一、入力identityが変わっていれば409、`rerun`は別Job・新snapshot。
- 違う点は**Job IDが同じか**だけだが、次に波及する: 一覧の行数（1行か2行か）、`attempts`の持ち方、Idempotency/CASの対象、
  終了状態の不変性、そして**出力ディレクトリ**（束C1は名前にJob IDを使う）。

## 2. 束C1（#51）の現在の挙動

retryで作られたJobは新しいJob IDを持つので、**同じ`output_stamp`・別のディレクトリ**になる。失敗した元Jobのディレクトリ
（所有marker、途中成果物があればそれも）はそのまま残る。上書きは起きない。

## 3. 解決案

| 案 | 内容 | 変更範囲 | 利点 | 懸念 |
|---|---|---|---|---|
| **A（推奨）** | **B7は現状維持**（retry＝`retry_of`付きの新Job）。UIが`retry_of`の連鎖を「1つの論理ジョブ」として1行に束ね、最新の試行を表に出す。Design Freezeの文言を「同一Job」→「同じsnapshotの同一論理ジョブ（内部は`retry_of`で連結した新Job）」へ追補する | UI（束C2）と設計書の追補のみ。API・DB・B6〜B9は無変更 | 既存のCAS・冪等性・終了状態の不変性・fencingをそのまま使える。公開APIの非互換なし。失敗した試行の証拠が独立したJobとして残る | 公開`Job`に連鎖を示すfieldが要る（`retry_of`は公開viewに既にある。根と最新を引くための読み取りは追加実装） |
| B | **B7を変更**し、retryを同じJob IDの新しいattemptにする（`failed`→`queued`へ戻す） | 状態遷移表（終了状態からの復帰）、`attempts`、replay表、B9イベント、`POST /jobs/{id}/retry`の応答（202＋Locationの新Job→同じJob）、既存試験 | Design Freezeの文言と文字どおり一致。出力ディレクトリも自然に1つ | **公開APIの挙動変更**と状態機械の変更（終了状態が終了でなくなる）。「結果不明時の同key再照合」など既存の安全設計を作り直す範囲が大きい。owner承認必須 |
| C | Design Freeze側を実装に合わせて書き換えるだけ（UIも2行表示） | 設計書のみ | 最小 | 画面で失敗ジョブと再試行ジョブが別々に並び、ownerが承認した操作感（再試行＝同じジョブ）と違う |

### 出力ディレクトリの扱い（案Aを採る場合の副次判断）

| 案 | 内容 | 利点 | 懸念 |
|---|---|---|---|
| **A-1（現状、推奨）** | retryごとに別ディレクトリ（名前のIDはそのJobのID） | 実装済み・試験済み。以前の試行のファイルに一切触れない。Job IDとディレクトリが1対1で、履歴/成果物削除（束C2）の対象検証が単純 | 失敗した試行のディレクトリ（多くはmarkerだけ）が出力先に残る |
| A-2 | 連鎖の**根のJob ID**で名前を作り、retryは同じディレクトリを引き継ぐ | 論理ジョブ1つにフォルダ1つ | markerの所有者を「連鎖」に広げる必要があり、所有検証が複雑になる。途中成果物を置き換える。成功したJobのIDとフォルダ名のIDが一致しなくなる |

A-1の懸念（空に近いディレクトリが残る）への対処案: 「失敗・取消で終わったJobのディレクトリがmarkerだけなら、そのJob自身が片付ける」。
**これは削除処理なので未実装**。束C2の削除設計（[c2-history-and-artifacts-design](c2-history-and-artifacts-design.md)）と一緒にowner判断を仰ぐ。

## 4. 判断をお願いしたい点

1. retryの契約: **案A**（B7維持＋UIで束ねる＋Design Freeze追補）でよいか。案Bを選ぶ場合は公開APIと状態機械の変更になるため、別PRで設計から行う。
2. 案Aの場合の出力先: **A-1**（retryごとに別ディレクトリ）のままでよいか。
3. 失敗・取消したJobの「markerだけのディレクトリ」を自動で片付けてよいか（既定は「片付けない」）。


## 5. D3-B 現在設定で再実行

Expert の結果・履歴から既存 B7 の `POST /api/v1/jobs/{id}/rerun` に接続した。元の入力の SHA-256 と一致する入力を受理し、現在の設定で別の論理ジョブを作る。対象選択、CAS と再照合の契約は [D3-B 実装記録](../ui3/d3b-artifacts-rerun.md) を参照。
