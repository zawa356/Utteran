# Phase 5a GUI 手動確認手順書

## 目的と安全上の注意

pywebviewのnative window、drag and drop、Explorer起動、themeの見え方など、自動testだけでは
確認しにくいPhase 5a GUIの項目をWindows実機で確認する。実録音の認識本文、token値、入力file名を
画面captureや報告へ転記しない。確認にはGit対象外の`output/_testdata`派生素材を使用する。

## 事前準備

PowerShellをrepository直下で開き、次を実行する。

```powershell
.\setup.ps1 -Profile gui -Yes
.\setup.ps1 -Profile cuda -Yes
.\run.ps1 -Profile cuda models list --json
.\run.ps1 -Profile cuda devices --json
.\gui.ps1
```

GUI profileにPyTorch／faster-whisperが入っていないこと、CUDA profile側に使用するASR modelと
必要ならpyannote modelが導入済みであることを確認する。旧`.venv`／`.venv-windows`は削除しない。

## 1. 起動と環境表示

1. native windowが開き、既定がdark theme、日本語であることを確認する。
2. hardware、作成済みprofile、導入済みmodel数、native構成が表示されることを確認する。
3. profileを切り替え、各profileで実際に利用可能なbackend、導入済みmodel、usableなdeviceだけが
   選択肢へ出ることを確認する。未導入modelや利用不能な`cuda:N`を表示してはならない。
4. 「再検出」で外部CLIの状態が再読込されることを確認する。
5. profileがない検査用checkoutでは、setupを促す案内が出てGUI自体は起動できることを確認する。

## 2. theme、言語、設定、token

1. 設定画面でlight／darkを切り替え、背景・境界・文字が読めることを確認する。
2. Englishへ切り替え、固定UI文言が切り替わることを確認する。CLI由来logは翻訳されなくてよい。
3. 既定profileと既定入出力directoryを保存し、GUI再起動後に復元されることを確認する。
4. 検査用Hugging Face read tokenを保存し、画面へ値が再表示されず「設定済み」だけになることを
   確認する。削除後は「未設定」になることを確認する。実tokenを報告やlogへコピーしない。
5. OS user configの`settings.json`にtheme、language、既定profile、既定directory以外の入力file履歴や
   tokenがないことを確認する。tokenはOS credential managerの`utteran`／`huggingface`だけに置く。

## 3. 文字起こしと進捗

1. `output/_testdata/clip_30s.mp4`を入力し、出力先をGit対象外の`output/gui-manual`にする。
2. fileをwindowへdropした場合も入力欄へ反映されることを確認する。
3. CUDA profile、導入済みfaster-whisper model、`cuda:0`、話者分離OFFを選び実行する。
4. audio／asr／diarization／merge／exportのstage表示、経過時間、可能な範囲のETA、折りたたみlogを
   確認する。話者分離OFFでもdiarization stageは無効処理として完了する。
5. exit 0後に出力file一覧が表示され、「出力フォルダを開く」でExplorerが正しいdirectoryを開く
   ことを確認する。結果本文のGUI閲覧はPhase 5bの範囲であり、Phase 5aにはない。
6. 同じ設定でresumeし、全stageがskipでも既存出力fileが一覧へ表示されることを確認する。
7. 話者分離ONで再実行する。8 GiB GTX 1070 TiでCUDA memory guardが停止する場合は、ASRを
   `cuda:0`のまま話者分離deviceをCPUへ変え、全stageが完走することを確認する。
8. folder入力では必要に応じて「サブフォルダも処理」、include／exclude globを指定し、file単位の
   開始／完了が詳細logに出ることを確認する。

## 4. 中断、停止検知、エラー案内

1. 3分以上の派生素材をforceで開始し、ASR中にキャンセルする。
2. 子process treeが終了し、画面が中断、exit 130、resume可能の案内になることを確認する。
   Task Managerに当該jobの`utteran.exe`／ffmpeg子processが残ってはならない。
3. 30秒以上progress eventがない状況では「処理は継続中」の応答待ち案内が出て、自動cancelや
   timeoutにならないことを確認する。
4. 入力なし、model未取得、token未設定、ffmpeg不在、memory budget超過を安全な検査環境で発生させ、
   入力／model／token／ffmpeg／memoryに応じた対処案内が表示されることを確認する。
5. 詳細logと進捗eventにtoken、認識segment／word／本文が含まれないことを確認する。

## 自動確認へ委ねる項目

- JSONLのschema、1行1 JSON、UTF-8、日本語、秘密mask、本文非包含、batch境界
- session keyの正／誤／欠落、HttpOnly cookie、loopbackのOS割当port
- 動的選択肢の生成、shellなしargv、profile GUIとcoreのimport分離
- fake process注入によるprocess tree killer呼出し、settings／keyring adapterの保存・復元

これらは`tests/test_progress_json.py`と`tests/test_gui.py`で回帰検査する。

## 5. Phase 5b 結果閲覧・検索・履歴

Phase 5bで追加した次の項目も、上記と同じWindows native windowで確認する。

1. 完了後の「結果を表示」からviewerが開き、ASR／話者分離のmodelとdevice、
   timestamp、話者色、統計がdark／lightで判読できる。
2. 2時間級約1,300 segmentで仮想scroll、日本語IME検索、highlight、一致移動、
   話者／時間filterを確認する。本文や検索語は報告に転記しない。
3. 履歴の絞り込み／sort／folder表示／個別削除と、形式・話者表示名・出力先を
   変更したexport-only再生成を確認する。上流4 stageは再実行されない。
4. Englishへ切り替え、新画面の固定文言が切り替わる。GUI再起動後に検索語が
   復元されず、参加者表示名はGUI設定でなく対象jobだけに保存される。

詳細な長時間性能、schema不一致、削除確認、privacy確認の手順は
[`Phase5b_GUI_手動確認手順書.md`](Phase5b_GUI_手動確認手順書.md)を参照する。
