# E-C2a — Windows 11 VM 検証の引継ぎ（Codex 向け）

[ADR](e-c2a-rust-safe-fs.md) の Windows 項目を実機で確認するための手順。Issue [#86](https://github.com/zawa356/Utteran/issues/86)。
**製品非接続の PoC。製品コード・製品版・既存 CI・Release は変更しない。合成データのみ。**

## 1. 固定 HEAD・担当範囲

- 対象 HEAD: Draft PR 本文の「VM 引継ぎ用の固定 SHA」に記載した commit を `git checkout --detach <SHA>` で使う（本書を含む commit 自身の SHA は本書に書けないため）。
- Cloud Linux（Claude Code）は固定 SHA 以降、Windows 実装ファイルを編集しない。並行編集をしない。
- VM 側で修正が必要な場合は、固定 SHA から別 branch（例: `codex/e-c2a-windows-vm`）を作り、変更してよいのは次に限る:
  `native/safe-fs/src/sys/windows.rs`、`native/safe-fs/tests/windows_only.rs`、新規 `native/safe-fs/tests/windows_vm_*.rs`、本書の結果欄。
  公開 API（`lib.rs`・`chain.rs`・`meta.rs`・`error.rs`・`name.rs`）を変える必要が出たら、変更せず理由を記録して戻す。
- 削除・rename・上書き・外部アプリ起動の機能を追加しない。試験の後始末は試験専用の temp 領域（`tempfile`）だけ。

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
git checkout --detach <固定SHA>
cd native
rustup show active-toolchain          # 1.97.0-x86_64-pc-windows-msvc
cargo fmt --check
cargo clippy -p utteran-safe-fs --all-targets --locked -- -D warnings
$env:SAFE_FS_TEST_ROOT = "C:\safefs-vm"
cargo test -p utteran-safe-fs --locked -- --nocapture *> core-tests.log
Select-String NOT_RUN core-tests.log
uvx --from maturin==1.16.0 maturin build --release --locked -m safe-fs-py/Cargo.toml -o dist -i (uv python find 3.11)
$tests = (Resolve-Path safe-fs-py\tests).Path
foreach ($v in "3.11","3.12") {
  uv venv -p $v "$env:TEMP\safefs-venv-$v"
  uv pip install --python "$env:TEMP\safefs-venv-$v\Scripts\python.exe" (Get-ChildItem dist\*.whl).FullName pytest==8.4.2
  Push-Location $env:TEMP
  & "$env:TEMP\safefs-venv-$v\Scripts\python.exe" -m pytest -q -p no:cacheprovider "$tests\test_binding.py"
  & "$env:TEMP\safefs-venv-$v\Scripts\python.exe" "$tests\bench.py" $env:SAFE_FS_TEST_ROOT
  Pop-Location
}
```

（期待結果: core 試験は全 PASS、NOT_RUN は §4 の条件付き項目のみ、binding は 3.11・3.12 とも PASS。）

## 4. 検証項目と期待結果

印: 〔自動〕＝`windows_only.rs`／`basic.rs` に試験あり、〔手動〕＝本書の手順で操作、〔管理者〕＝管理者権限が必要、〔要実装〕＝VM 側で限定的な試験追加が必要。

| ID | 項目 | 手順 | 期待結果 |
|---|---|---|---|
| W-01 | ローカル NTFS の受理 | 〔自動〕`local_ntfs_root_is_accepted` | `C:\…` と `\\?\C:\…` が同じ identity 列 |
| W-02 | 保持中の祖先の rename・削除拒否 | 〔自動〕`held_ancestors_cannot_be_renamed_or_deleted` | 3 階層とも rename・rmdir が ERROR_SHARING_VIOLATION(32) か ERROR_ACCESS_DENIED(5)。解放後は成功 |
| W-03 | 既存 DELETE handle がある場合 | 〔自動〕`existing_delete_access_handle_blocks_pinning` | `busy` |
| W-04 | **別 process** からの rename／削除 | 〔手動〕PoC の保持中（Python で `DirChain.open` したまま待機）に、別の PowerShell から `Rename-Item`・`Remove-Item`・Explorer での名前変更を試す | すべて失敗し、名前・内容が不変。`close()` 後は成功 |
| W-05 | 読取り中の writer 拒否 | 〔自動〕`open_writer_blocks_read_and_reader_blocks_writer`、`concurrent_modification_during_read_is_refused_or_detected` | writer がいれば `busy`、読取り中の追記は 32 |
| W-06 | junction | 〔自動〕`junctions_are_refused_in_walk_and_as_children` | walk・子の open は `link_or_reparse`、同名作成は `already_exists`、target 不変 |
| W-07 | symlink | 〔自動〕`symlinks_are_refused_when_creatable`（開発者モード ON か管理者で実行） | NOT_RUN でなく PASS になる環境で 1 回記録 |
| W-08 | 8.3 短縮名 | 〔自動〕`short_8dot3_alias_is_refused`。8.3 が無効なら〔管理者〕`fsutil 8dot3name set C: 0` を一時 VHD 上で設定して再実行（システム drive の設定は変えない） | `name_aliased` |
| W-09 | 大小文字・Unicode・長パス | 〔自動〕`ascii_case_variant_reaches_the_same_directory`、`unicode_and_long_paths_beyond_max_path`。`LongPathsEnabled` が 0 と 1 の両方で実行 | 同一 identity、MAX_PATH 超でも PASS（レジストリ設定に依存しない） |
| W-10 | UNC・device 名前空間 | 〔自動〕`unc_and_device_namespace_roots_are_refused` | `invalid_root` |
| W-11 | SUBST | 〔自動〕`subst_drive_is_refused` | `invalid_root`。試験後 `subst` の割当てが残っていないこと |
| W-12 | ネットワーク drive | 〔管理者〕`New-SmbShare` で専用 dir を共有し `net use Z: \\localhost\<share>`、`SAFE_FS_REMOTE_ROOT=Z:\` で `mapped_network_drive_is_refused` | `remote_volume`（または `invalid_root`）。終了後に割当てと共有を解除 |
| W-13 | 非 NTFS | 〔管理者〕FAT32 と exFAT（可能なら ReFS）の VHD を作成・mount し、`SAFE_FS_NON_NTFS_ROOT=<drive>:\dir` で `non_ntfs_volume_is_refused` | `unsupported_file_system` |
| W-14 | 保持中の空 leaf の reparse 化 | 〔要実装〕`push_new` 直後、別 process（または別 thread で別 handle）が `FSCTL_SET_REPARSE_POINT` で leaf を junction 化し、その後 `create_file` する | 期待: 作成は保持した dir 自体に入り、junction の target 側に何も作られない。junction 化自体が共有違反で失敗する場合はその結果を記録。いずれでもなければ **BLOCKED** として報告 |
| W-15 | 子 process の kill と handle 解放 | 〔自動〕`kill_mid_write_releases_handles_and_keeps_partial` | 部分ファイルが残り、他に何も作られず、kill 後に rename 可能 |
| W-16 | ACL 継承 | 〔手動〕継承 ACE を付けた専用 dir の下で `push_new`／`create_file` し、`icacls` で親・子を比較。継承を無効にした dir（読取り専用 ACE）では `permission_denied` | 子は親の継承可能 ACE を持つ。拒否時は何も作られない |
| W-17 | 権限拒否 | 〔手動〕`icacls <dir> /deny %USERNAME%:(OI)(CI)(W)` 等で書込み拒否・一覧拒否を作り、`create_file`／`open_dir` | `permission_denied`、既存内容不変（終了後に ACE を戻す） |
| W-18 | OneDrive placeholder | 〔手動、OneDrive がある場合のみ〕専用の空フォルダで「オンラインのみ」のファイルを作り `read_file` | `link_or_reparse`（hydration が起きないこと）。OneDrive が無ければ NOT_RUN |
| W-19 | Defender・索引の干渉 | 〔手動〕W-02／W-05 をリアルタイム保護 ON のまま 3 回 | 結果が変わらない（共有違反の誤検知がないこと） |
| W-20 | wheel の配布単体性 | 〔手動〕Rust toolchain を PATH から外した新しい PowerShell で、wheel を入れた venv から import と `capabilities()` | `platform=windows, ancestors_pinned=enforced, dir_creation=atomic` |
| W-21 | SAC／WDAC | 〔手動、可能な場合〕Smart App Control 評価モードで `.pyd` の読込み | 結果（許可／ブロック）を記録するだけ。回避策は取らない |

## 5. 判定と報告

- 各 ID を PASS／FAIL／NOT_RUN／BLOCKED で記録し、NOT_RUN は理由を書く。NOT_RUN を PASS 扱いしない。runner の結果を VM の結果に流用しない。
- 記録するもの: Windows の版・build、VM か実機か、標準ユーザー／管理者、`rustc -vV`、固定 SHA、`cargo test` の summary 行、NOT_RUN の行、W-04／W-12／W-13／W-16／W-17 の操作ログ（パスは合成 dir のみ）。
- **E-C2b で Windows を解禁する条件**（提案、オーナー承認事項）: W-01〜W-06、W-09〜W-11、W-14、W-15、W-16、W-17、W-20 が PASS。W-12・W-13 が PASS か、少なくとも FAIL でないこと。
  W-14 が BLOCKED の場合は、保持中の leaf に対する reparse 化の扱いを設計し直すまで解禁しない。
- 結果は #86 へのコメントか、VM 用 branch の Draft PR に記録する。
