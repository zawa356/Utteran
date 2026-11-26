# E-C2a/E-C2b — Windows 11 VM 検証の引継ぎ（Codex 向け）

> 2026-11-26統合候補: [Windows統合受入](windows-integration-acceptance.md)を参照。
> 必須項目が1件でもPASS以外ならWindows製品再出力を無効にする（FAILだけでなくNOT_RUN/BLOCKEDも含む）。
> 現候補はMSVC/wheel・対話操作・配布等が未受入のため無効。Rust core/POSIX回帰を維持し、
> native adapter試験だけテスト内で明示的に有効化。製品APIのunsupported/no writesを別途必須検証する。
> 今回のbranch/編集範囲・統合手順は#22の統合指示が下記の旧個別指示に優先する。

[ADR](e-c2a-rust-safe-fs.md) §13 の Windows 再出力（0.1.70、Rust core）を実機で確認する手順。Issue [#86](https://github.com/zawa356/Utteran/issues/86)／[#87](https://github.com/zawa356/Utteran/issues/87)、Draft [#88](https://github.com/zawa356/Utteran/pull/88)。
合成データのみ。Ready 化・merge・tag・Release はしない。

## 1. 固定 HEAD・担当範囲

- 対象: #88 本文「VM 引継ぎ用の固定 SHA」の commit を `git checkout --detach <SHA>`。
- Claude（Cloud Linux）は固定 SHA 以降、下記の Codex 担当ファイルを編集しない。
- Codex は固定 SHA から `codex/e-c2b-windows-vm` を作り、変更してよいのは次に限る（結果は #88 に取り込む）:
  `native/safe-fs/src/sys/windows.rs`、`native/safe-fs/tests/windows_only.rs`、新規 `native/safe-fs/tests/windows_vm_*.rs`、
  `tests/test_ec_reexport_native.py`（Windows 専用の試験の追加）、`src/utteran_gui/reexport_files_native.py` の `supported()`（無効化が必要な場合のみ）、本書の結果欄。
  公開 API（`lib.rs`・`chain.rs`・`meta.rs`・`error.rs`・`name.rs`）や service を変える必要が出たら、変えずに理由を報告する。
- 削除・rename・上書き・外部アプリ起動の機能を追加しない。後始末は試験専用の temp 領域と使い捨て VHD だけ。

## 2. 前提環境

| 項目 | 値 |
|---|---|
| OS | Windows 11（版・build 番号を記録）。システム drive がローカル NTFS |
| 権限 | 標準ユーザーで実行する試験と、管理者が必要な試験（§4 の印）を分ける |
| Rust | rustup で `1.97.0`（`native/rust-toolchain.toml` が自動選択）。MSVC target は Visual Studio Build Tools の「C++ によるデスクトップ開発」（link.exe）が必要 |
| Python | `uv` で 3.11・3.12（`uv python install 3.11 3.12`） |
| fixture 置き場 | 専用の空 dir（例 `C:\safefs-vm`）。`SAFE_FS_TEST_ROOT` にその絶対パスを設定すると全 fixture がその下に作られ、各試験の終了時に消える |
| 禁止 | 実音声・実転写・個人パス・token を使わない。OneDrive の個人データ配下で試験しない（専用の空フォルダのみ） |

## 3. 基本手順（PowerShell。標準ユーザー）

```powershell
function Check { if ($LASTEXITCODE -ne 0) { throw "failed: exit $LASTEXITCODE" } }
git checkout --detach <固定SHA>; Check
cd native
rustup show active-toolchain; Check      # 1.97.0-x86_64-pc-windows-msvc
cargo clippy -p utteran-safe-fs --all-targets --locked -- -D warnings; Check
$env:SAFE_FS_TEST_ROOT = "C:\safefs-vm"     # 専用の空 dir
$env:SAFE_FS_REQUIRE = "junction,symlink,subst"
cargo test -p utteran-safe-fs --locked -- --nocapture *> core-tests.log; Check
Select-String NOT_RUN core-tests.log     # remote / non_ntfs / 8dot3 だけが許される
uvx --from maturin==1.16.0 maturin build --release --locked -m safe-fs-py\Cargo.toml -o dist -i (uv python find 3.11); Check
$wheel = (Get-ChildItem dist\utteran_native_fs-*.whl).FullName
cd ..
uv sync --extra dev --extra gui --locked; Check
uv pip install --no-deps $wheel; Check
uv run --no-sync pytest -q -p no:cacheprovider tests\test_ec_reexport_native.py tests\test_ec_reexport.py; Check
```

（期待結果: core・製品の native 再出力試験がすべて PASS。W-12／W-13 は §4 の環境を用意し、環境変数を付けて再実行する。）

## 4. 検証項目と期待結果

印: 〔自動〕＝`windows_only.rs`／`basic.rs`／`tests/test_ec_reexport_native.py` に試験あり、〔手動〕＝本書の手順、〔管理者〕＝管理者権限が必要、〔要実装〕＝VM 側で試験を追加。
**システム drive（C:）の設定・保護機能（Defender、SAC、8.3 設定、LongPaths 等）は変更しない。** 特殊な FS・8.3 は使い捨て VHD（`New-VHD`／`diskpart` で作成し、終了後に detach・削除）でだけ扱う。
各 native command の後に `if ($LASTEXITCODE -ne 0) { throw }` を置き、必須項目の NOT_RUN を PASS 扱いしない。

| ID | 必須 | 項目 | 手順 | 期待結果 |
|---|---|---|---|---|
| W-01 | ○ | ローカル NTFS の受理 | 〔自動〕`local_ntfs_root_is_accepted` | PASS |
| W-02 | ○ | 保持中の祖先の rename・削除拒否 | 〔自動〕`held_ancestors_cannot_be_renamed_or_deleted` | 32／5 で拒否、解放後は成功 |
| W-03 | ○ | 既存 DELETE handle | 〔自動〕`existing_delete_access_handle_blocks_pinning` | `busy` |
| W-04 | ○ | **別 process** の rename／削除 | 〔手動〕Python で `DirChain.open` を保持したまま、別 PowerShell と Explorer から `Rename-Item`／`Remove-Item`／名前変更 | すべて失敗、名前・内容不変。`close()` 後は成功 |
| W-05 | ○ | 読取り中の writer | 〔自動〕`open_writer_blocks_read_and_reader_blocks_writer` ほか | `busy`／共有違反 |
| W-06 | ○ | junction | 〔自動〕`junctions_are_refused_in_walk_and_as_children`、`test_windows_junction_parent_and_held_parent_rename` | 拒否、target 不変 |
| W-07 | ○ | 実 symlink | 〔自動〕`SAFE_FS_REQUIRE=junction,symlink,subst` を付けて `symlinks_are_refused_when_creatable`（管理者または開発者モード） | NOT_RUN 不可、PASS |
| W-08 |  | 8.3 短縮名 | 〔管理者〕使い捨て VHD を NTFS で作り、**その VHD に対してだけ** `fsutil 8dot3name set <VHD>: 0`、`SAFE_FS_TEST_ROOT=<VHD>:\` で `short_8dot3_alias_is_refused` | `name_aliased` |
| W-09 | ○ | Unicode・長パス | 〔自動〕`unicode_and_long_paths_beyond_max_path`（LongPaths の設定は変えず現状のまま） | PASS |
| W-10 | ○ | UNC・device 名前空間 | 〔自動〕`unc_and_device_namespace_roots_are_refused` | `invalid_root` |
| W-11 | ○ | SUBST・per-logon の割当て | 〔自動〕`subst_drive_is_refused`、`per_logon_drive_mapping_is_not_followed`。〔手動〕使い捨て VHD を空き letter（例 `V:`）に mount し、`subst W: V:\` の `W:\` と、global の `V:\` を比べる | `W:` は `invalid_root`、`V:` は受理。既存の global 名は通常 API で上書きできない（DefineDosDevice は per-logon に作る）ことを記録。`C:` は使わない |
| W-12 | ○ | SMB／mapped drive | 〔管理者〕専用 dir を `New-SmbShare`、`net use Z: \\localhost\<share>`、`SAFE_FS_REMOTE_ROOT=Z:\` で `mapped_network_drive_is_refused` | `remote_volume` か `invalid_root`（どちらも拒否）。終了後に解除 |
| W-13 | ○ | FAT32／exFAT | 〔管理者〕使い捨て VHD を FAT32 と exFAT で各 1 回、`SAFE_FS_NON_NTFS_ROOT=<VHD>:\dir` で `non_ntfs_volume_is_refused` | `unsupported_file_system`。ReFS は環境がなければ NOT_RUN（未対応のまま） |
| W-14 | ○ | 保持中の dir の reparse 化 | 〔要実装〕別 process を同期 barrier（名前付き event かファイル）で止め、(a) `push_new` 直後・marker 前、(b) marker 後・各ファイルの間、の各時点で保持中の新規 dir と親 dir に `FSCTL_SET_REPARSE_POINT`（junction）を試み、可能なら設定→解除も行う。その後 `create_file`／`verify_held()` | junction の target 側と元ジョブ側に何も作られない・変更されない（identity・hash・一覧で確認）。reparse 化自体が失敗する、または `verify_held()`／作成が `link_or_reparse` で止まるなら PASS。target 側に作られたら **FAIL → Windows 無効化** |
| W-15 | ○ | kill と handle 解放 | 〔自動〕`kill_mid_write_releases_handles_and_keeps_partial` | PASS |
| W-16 |  | ACL 継承 | 〔手動〕継承 ACE を付けた専用 dir で再出力し `icacls` で比較 | 子は親の継承 ACE を持つ（記録のみ） |
| W-17 | ○ | 権限拒否 | 〔手動〕専用 dir に `icacls /deny %USERNAME%:(OI)(CI)(W)` を付けて再出力 | `output_readonly`、何も作られない。終了後に ACE を戻す |
| W-18 |  | OneDrive placeholder | 〔手動、OneDrive がある場合のみ〕専用の空フォルダで | `source_link`／`output_parent_link`。無ければ NOT_RUN |
| W-20 | ○ | wheel の単体性 | 〔手動〕Rust を PATH から外した PowerShell で `capabilities()` | `windows`／`enforced`／`atomic` |
| W-21 |  | SAC／WDAC | 〔手動、可能な場合〕結果の記録のみ（回避しない、設定を変えない） | 記録 |
| W-22 | ○ | 製品の再出力（native 層） | 〔自動〕wheel を製品 venv に入れて `pytest tests/test_ec_reexport_native.py tests/test_ec_reexport.py` | 全 PASS（Windows 専用の junction／rename 試験を含む） |
| W-23 | ○ | 配布物 | 〔手動〕`build.ps1` で installer／portable を作り、portable で `--next-ui-v1` の再出力を 1 回（合成音声の完了ジョブから） | `utteran_native_fs` が同梱され、新しいフォルダに全形式が出る。元ジョブのフォルダ不変 |

## 5. 判定と報告

- 各 ID を PASS／FAIL／NOT_RUN／BLOCKED で記録（NOT_RUN は理由）。runner の結果を VM の結果に流用しない。
- 記録: Windows の版・build、VM／実機、標準ユーザー／管理者、`rustc -vV`、固定 SHA、`cargo test`／`pytest` の summary 行、NOT_RUN の行、手動項目の操作ログ（合成 dir のみ）。
- **Windows 再出力を有効のまま残す条件**: 「必須」○ の全項目が PASS。W-14 が FAIL／BLOCKED、または W-02／W-04／W-06／W-07／W-12／W-13 のどれかが FAIL なら、
  `reexport_files_native.supported()` を常に False にする修正（1 行）を同じ branch に入れて報告する。
- 結果は Codex 用 branch の commit と #88 へのコメントで返す。Claude 側はその branch を #88 に取り込む。
