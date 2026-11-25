# E-C2a — Rust 共通安全ファイル操作基盤の技術検証（ADR・PoC記録）

Issue [#86](https://github.com/zawa356/Utteran/issues/86)（親 [#82](https://github.com/zawa356/Utteran/issues/82)、後続 [#87](https://github.com/zawa356/Utteran/issues/87)）。
指示書は [#86 コメント](https://github.com/zawa356/Utteran/issues/86#issuecomment-7156697752)（2026-11-25 JST）。
**PoC・技術選定のみ。製品非接続、製品版は据え置き（`pyproject.toml`／`uv.lock`／`変更履歴.md` の版は変更しない）。**
採用方式・最終 API・配布方式はオーナー承認事項で、本書は判断材料と推奨を示す。

- 基点 main: `57ba960f58b38dcd37a1b06ee0599ca9b2da1a1f`（#85 統合済み）。E-C1 [PR #83](https://github.com/zawa356/Utteran/pull/83) は Draft のまま未統合（調査時 HEAD `d849c0d`）で、本 PoC はその製品コードを変更・import しない。
- 成果物: [`native/`](../../../native/)（独立 Cargo workspace）、PoC 専用 CI [`.github/workflows/native-poc.yml`](../../../.github/workflows/native-poc.yml)、Windows VM 引継ぎ [e-c2a-windows-vm-handoff.md](e-c2a-windows-vm-handoff.md)。
- 最終 HEAD・CI run・各 OS の試験件数は Draft PR 本文に記録する（本書の数値は記載時点の実測）。

## 1. 結論（推奨）

| 項目 | 推奨 | 状態 |
|---|---|---|
| 基盤方式 | **自前の薄い core crate `utteran-safe-fs`**。POSIX は `rustix`（safe API、自前 unsafe 0）、Windows は `windows-sys` の `NtCreateFile`（RootDirectory 相対）を直接使用。`fs_at`・`cap-std` は不採用（§2） | Linux 実証済み。Windows は runner で自動試験、**VM 受入は未実施** |
| Python 接続 | 別 crate `utteran-safe-fs-py`（PyO3 0.30.1、`abi3-py311`）を maturin 1.16.0 で 1 本の abi3 wheel に | Linux で 3.11／3.12 の新規 venv から install・import・試験 PASS |
| 製品への組込み | 内部 wheel を `gui` 実行環境と PyInstaller onedir に同梱（推論 venv には入れない）。Hatchling の製品 wheel は純 Python のまま | 設計のみ（§7）。installer 変更は E-C2b |
| Windows 再出力の解禁 | **VM で §9 の Windows 項目が PASS するまで BLOCKED を維持** | 現行の `reexport_platform_unsupported` を変更しない |
| Linux の切替 | 既存 Python POSIX 実装（E-C1）を回帰基準として保持。Rust 版は同等以上と確認できた経路だけを #87 で切替 | 本 PoC で同等の拒否・検出を確認（§5.1）。製品切替は未実施 |

判断の根拠: Windows で必要な 2 点（**FILE_SHARE_DELETE を付けない祖先保持**と**作成と同時の handle 取得**）を、
既存 crate はどちらも公開 API で満たさない（§2.2）。不足部分だけを補う「最小補完」は、結局 Windows の open 経路全体の置換になる。
自前実装は NtCreateFile 1 種類の呼出しと情報取得の FFI に限られ（`unsafe` ブロック 16、すべて `sys/windows.rs`）、依存は 8 crate で済む。

## 2. 候補調査（2026-11-25 に crates.io の固定版ソースを取得して確認）

### 2.1 版・依存・ライセンス・保守

| crate | 確認版 | 公開日 | ライセンス | MSRV | 実行時依存（全 target） | RustSec（advisory-db 取得時点） |
|---|---|---|---|---|---|---|
| `fs_at` | 0.2.1（sha256 `14af6c96…`） | 2024-07-23（以後リリースなし） | Apache-2.0 | 1.71 | 19 crate（`nix` 0.29、`windows-sys` 0.52＋`windows-targets` import-lib 8 crate、`aligned` 等） | 該当なし |
| `cap-std`＋`cap-fs-ext` | 4.0.3（`c1ec78e2…`／`56ff379b…`） | 2026-08-20 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT | 記載なし | 約 40 crate（`windows-sys` 0.59／0.60／0.61 の 3 版併存、`io-lifetimes` 2／3 併存、`ipnet`、`winx`、`fs-set-times` 等） | 該当なし |
| `rustix` | 1.1.6（`e5e76742…`） | 2026-10-27 | 同上（3 択） | 1.65 | `bitflags`、`errno`、`libc`、`linux-raw-sys` | 該当なし |
| `windows-sys` | 0.61.2（`ae137229…`） | 2025-10-06 | MIT OR Apache-2.0 | 1.71 | `windows-link`（import lib 不要） | 該当なし |
| `pyo3` | 0.30.1 | 2026-10-14 | MIT OR Apache-2.0 | 1.85 | binding 側のみ | 該当なし |
| `maturin` | 1.16.0 | 2026-10-29 | MIT OR Apache-2.0 | 1.89 | build 時のみ | — |

採用構成の依存は `rustix`／`bitflags`／`errno`／`libc`／`linux-raw-sys`／`windows-sys`／`windows-link` の 7 crate（core 自身を含め 8）。
`cargo deny check`（cargo-deny 0.22.1、`native/deny.toml`：advisories は unmaintained・yanked を含め全件拒否、許可ライセンスは MIT／Apache-2.0／Apache-2.0 WITH LLVM-exception／Unicode-3.0、
crates.io 以外の source 拒否）は `advisories ok, bans ok, licenses ok, sources ok`。同じ設定で `fs_at`・`cap-std` 構成も advisories ok（脆弱性での不採用ではない）。

### 2.2 重点確認（ソースで確認した事実）

- **fs_at の共有モードは固定**: `src/win.rs` の `do_create_file` が `share_access = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE` を常に渡す（コメント「This should be exposed」）。
  利用者は DELETE 共有を外せないため、保持した祖先を他 process が rename・削除できる。`mkdir_at` は Windows では `NtCreateFile(FILE_CREATE)` で handle を返すが、Unix では `mkdirat`→`openat` の 2 段。
  想定外の `IO_STATUS_BLOCK.Information` で `unimplemented!()`（panic）に到達する経路がある。2024-07 以降更新がなく、`windows-sys` 0.52・`nix` 0.29 と旧版を引き込む。
- **cap-std の目的は sandbox（root より下から出さない）で、identity 固定ではない**。Windows の dir handle は `FILE_SHARE_DELETE` なしで開き（`dir_utils.rs` の `dir_options()`、`oflags.rs` で dir の DELETE 共有を常に除去）、
  相対 open は `NtCreateFile` の RootDirectory を使う（`create_file_at_w.rs`）点は本方式と同じ。しかし **`create_dir` は `GetFinalPathNameByHandleW` で得た絶対パス＋`std::fs::create_dir` のパス操作**（`create_dir_unchecked.rs`）で、
  戻り値は `()`。作成と handle 取得が分離し、パスベースの操作になる。`..` は内部で手動正規化（`open_unchecked.rs`）し、ファイル identity（`FILE_ID_INFO`）・ボリューム種別・リモート判定の API はない。
- **rustix**: `openat`・`mkdirat`・`statat(AT_SYMLINK_NOFOLLOW)`・`fstat`・`statx(STATX_MNT_ID)`・`fstatfs`・`fsync`・`Dir::read_from`・`openat2(RESOLVE_*)` がすべて safe API で揃う。
- **windows-sys 0.61.2**: `NtCreateFile`・`NtQueryVolumeInformationFile`（`Wdk_Storage_FileSystem`）、`GetFileInformationByHandleEx`（`FILE_ID_INFO`・`FILE_ATTRIBUTE_TAG_INFO`・`FILE_STANDARD_INFO`・`FILE_BASIC_INFO`・`FILE_ID_BOTH_DIR_INFO`）、
  `GetVolumeInformationByHandleW`、`GetFinalPathNameByHandleW`、`FILE_FS_DEVICE_INFORMATION` を確認。
- **openat2**: 単一 component の `openat(O_NOFOLLOW)` に対して加わる効果は `RESOLVE_NO_XDEV`（mount 越え拒否）だけで、symlink 系の flag は 1 要素では意味を持たない。
  本 PoC は代わりに `statx(STATX_MNT_ID)` の mount id を identity に加え、子の open で比較する（bind mount も区別できる。§5.1）。カーネルが mount id を返さない場合は能力 `mount_identity = not_provided` と報告し、成功扱いしない。

### 2.3 操作ごとの充足表

| 必要な操作 | fs_at 0.2.1 | cap-std 4.0.3 | rustix 1.1.6（POSIX） | windows-sys（自前） |
|---|---|---|---|---|
| root からの 1 要素ずつの no-follow walk と保持 | ○（open_dir_at） | △（sandbox 内の相対 path。祖先 identity なし） | ○ | ○ |
| 祖先の rename／削除の拒否（Windows） | **×（DELETE 共有固定）** | ○（dir は DELETE 共有なし） | 不可能（OS 能力なし） | ○ |
| 新規 dir の作成と handle 取得の一体化 | Windows ○／Unix × | **×（path 操作、handle なし）** | 不可能（mkdir は fd を返さない） | ○（`FILE_CREATE`） |
| 排他 file 作成（no-follow） | ○ | ○ | ○ | ○ |
| identity／volume／link 数／reparse tag | △（std Metadata のみ） | △ | ○ | ○ |
| FS 種別・リモート判定 | × | × | ○（`fstatfs`） | ○ |
| 読取り中の他 writer 拒否（Windows） | ×（共有固定） | 設定可（file は `share_mode`） | 不可能 | ○ |
| 上書き・削除 API を持たない | ×（unlink_at 等あり） | ×（remove 系あり） | 呼ばなければよい | 呼ばなければよい |

上流修正（fs_at に share mode を公開する）・薄い fork も比較したが、fork は全 Windows 経路の保守を引き受ける点で自前と同じで、依存が増える分だけ不利。
cap-std への `create_dir` の handle 返却の追加は sandbox 設計（`..` 解決・ambient authority の型）と整合させる必要があり、PoC の範囲で上流合意を待てない。**自前の薄い層を推奨**し、
将来 cap-std 等が要件を満たした時点で `sys/` だけを差し替えられるよう公開 API を OS 非依存に保つ。

## 3. PoC の構成

```
native/                        # 独立 Cargo workspace（製品の pyproject／uv／CI とは無関係）
  Cargo.toml  Cargo.lock  rust-toolchain.toml (1.97.0)  deny.toml
  safe-fs/      crate utteran-safe-fs（core。Python・PyO3・UI 非依存）
    src/lib.rs chain.rs name.rs error.rs meta.rs hooks.rs sys/{mod,unix,windows}.rs
    tests/basic.rs unix_races.rs windows_only.rs common/mod.rs
  safe-fs-py/   crate utteran-safe-fs-py（cdylib、PyO3。ロジックなし）＋ pyproject.toml（maturin）
    tests/test_binding.py bench.py
```

### 3.1 公開 API（Rust）

- `DirChain::open_absolute(&str)`：volume root から全要素を保持。`leaf()`、`depth()`、`identities()`、`fs_kind()`、`push_existing(&Name)`、`push_new(&Name) -> DirCreation`、
  `verify_reachable()`（名前で再 walk し identity 照合。検出のみ）、`find_in_ancestors(&Name)`（各保持 dir 内に名前があるか、link を辿らず観測）。
- `Dir`：`metadata()`、`is_empty()`、`open_dir`、`create_dir -> (Dir, DirCreation)`、`probe`、`read_file(&Name, max) -> ReadOutcome`、`create_file -> NewFile`、`sync()`。
- `NewFile`：`write_all`（1 MiB 単位、失敗は以後も失敗）、`written()`、`finish() -> Metadata`（fsync／`F_FULLFSYNC`／`FlushFileBuffers` 後にサイズ照合）。Drop は閉じるだけで削除しない。
- `Name::new(&str)`：単一 component の移植可能な検証（空・`.`・`..`・`/`・`\`・NUL・制御文字・`:`（ADS・drive）・`<>"|?*`・末尾の空白/ドット・先頭空白・DOS device 名（拡張子付き・上付き数字含む）・255 超を拒否。Unicode は正規化しない）。
- `DirChain::verify_held()`：全保持 handle の種別・identity を handle 越しに再確認（Windows で保持中の dir が reparse 化された場合の検出）。
- `capabilities()`：OS ごとの保証を `Guarantee::{Enforced, DetectedOnly, NotProvided}` と `DirCreation::{Atomic, ReopenedByName}` で返す。同じ名前の操作でも保証が違うことを型で表す。
- `Error { code: ErrorCode, os_code: Option<i64> }`：固定コード 22 種、`Display` はコード文字列のみ。`Name`・`Dir` の `Debug` も値を出さない。

**公開しないもの**: 生 fd／HANDLE、`std::fs::File`、OS 型、Python 型。既存データへの書込み・truncate・上書き・rename・削除の API。
`test-hooks` feature（決定的な競合点）は core の dev-dependency でだけ有効になり、binding・release build には含まれない。

### 3.2 unsafe 境界

- crate 全体を `#![deny(unsafe_code)]` とし、`sys/windows.rs` だけ `#![allow(unsafe_code)]`。POSIX 経路の自前 unsafe は 0（rustix の safe API）。
- Windows の `unsafe` ブロック 16 箇所、すべて `// SAFETY:` 注記（clippy `undocumented_unsafe_blocks = deny`）。契約: FFI へ渡すポインタは呼出し中生存する正しいサイズの local buffer、
  成功 status 直後に HANDLE を 1 回だけ `OwnedHandle` へ移し、以後は `std::fs::File` 経由でのみ使う。可変長の `FILE_ID_BOTH_DIR_INFO` の走査は範囲検査と `read_unaligned` を使う。
- clippy で `unwrap_used`・`expect_used`・`panic`・`indexing_slicing` を deny（ライブラリコード）。panic は binding で Python の `PanicException` になる（プロセスは落ちない）。

## 4. OS 別の方式

### 4.1 POSIX（Linux／macOS 共通、rustix）

- root: `/` を `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` で開く。`//` 始まり、空・`.`・`..`・末尾 `/` を含む path は `invalid_root`。
- 各要素: `openat(parent, name, O_DIRECTORY|O_NOFOLLOW|O_NOCTTY|O_CLOEXEC)`、`fstat` で dir を再確認。Linux は `statx(STATX_MNT_ID)` の mount id を記録。
- 新規 dir: `mkdirat(0700)` → `openat(O_NOFOLLOW)` で開き直し、自分の所有かつ空であることを確認（違えば `claim_lost`）。**同じユーザーが空の dir に差し替えた場合は区別できない**（E-C1 と同じ残留）。
- 新規 file: `openat(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`。dangling symlink を含む既存名は `already_exists`。
- 読取り: `O_RDONLY|O_NOFOLLOW|O_NONBLOCK`（FIFO で止まらない）で開き、通常ファイル・link 数 1・上限以下を確認、上限＋1 バイトまで読み、前後の `fstat`（identity・サイズ・mtime・ctime）一致とバイト数一致を要求。
  **これは検出の補助（heuristic）で、同じサイズの上書きが同じ timestamp tick 内に起きると検出できない。完全性は呼出し側の digest 照合（E-C1 の SHA-256）で担保する**（独立レビュー P2-5）。
  型の確認は open の後（device node を開く副作用は `O_NONBLOCK|O_NOCTTY` で抑えるのみ）。
- FS 種別: Linux `fstatfs.f_type`、macOS `f_fstypename` と `MNT_LOCAL`。leaf がリモートなら `remote_volume`、**allow-list 外なら `unsupported_file_system`**
  （Linux: ext4・xfs・btrfs・tmpfs・zfs・overlay、macOS: apfs・hfs。CephFS・Lustre 等の未知の FS を local と誤認しない。独立レビュー P2-1）。

### 4.2 Windows（ローカル NTFS、windows-sys）

- root: `C:\a\b` か `\\?\C:\a\b` のみ。UNC（`\\server`、`\\?\UNC\`）、`\\.\`、`\\?\GLOBALROOT`・`\\?\Volume{…}`、drive 相対 `C:x`、`\x`、`/` 区切り、末尾 `\`、各要素の移植可能規則違反は `invalid_root`。
  drive letter は **global の DOS device 名前空間**で解決する（`\\?\GLOBALROOT\GLOBAL??\C:\`）。`\\?\C:\` はログオンごとの device map を通り、同じユーザーが `DefineDosDevice`／`subst` で `C:` を含む letter を
  別 volume に向けられるため（独立レビュー P1-1）。per-logon の subst・mapped drive は global に無いので `invalid_root`。これを `CreateFileW(FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT)`、共有 READ|WRITE で開き、
  (1) `GetFinalPathNameByHandleW(VOLUME_NAME_NONE)` が `\` ちょうど（SUBST・フォルダへ割り当てた drive を拒否）、(2) `FileFsDeviceInformation` が `FILE_DEVICE_DISK` かつ `FILE_REMOTE_DEVICE`／`FILE_REMOTE_DEVICE_VSMB`（Windows Sandbox・container の共有）なし、
  (3) `GetVolumeInformationByHandleW` の FS 名が `NTFS`（ReFS・FAT・exFAT 等は `unsupported_file_system`）。
- 各要素: `NtCreateFile(RootDirectory=親, 1 要素名, FILE_OPEN, FILE_DIRECTORY_FILE|FILE_OPEN_REPARSE_POINT|FILE_SYNCHRONOUS_IO_NONALERT)`、
  access `FILE_LIST_DIRECTORY|FILE_TRAVERSE|FILE_READ_ATTRIBUTES|SYNCHRONIZE`、**共有 `FILE_SHARE_READ|FILE_SHARE_WRITE`（DELETE なし）**。
  開いた後 `FILE_ATTRIBUTE_TAG_INFO` で reparse 属性（junction・symlink・mount point・OneDrive placeholder 等すべて）を拒否、`FILE_ID_INFO` の volume serial が親と一致、
  `GetFinalPathNameByHandleW` の最終要素が要求名と一致（完全一致か ASCII の大小文字違いのみ。8.3 短縮名や非 ASCII の大小文字違いは `name_aliased`）。
- 新規 dir: `NtCreateFile(FILE_CREATE, FILE_DIRECTORY_FILE)` が**作成した dir の handle を返す**（`DirCreation::Atomic`）。同じ共有モードで保持。SecurityDescriptor は NULL＝親の継承可能 ACE を継承。
- 新規 file: `FILE_CREATE|FILE_NON_DIRECTORY_FILE|FILE_OPEN_REPARSE_POINT`、`FILE_GENERIC_WRITE`、共有 READ のみ。
- 読取り: `FILE_READ_DATA`、**共有 `FILE_SHARE_READ` のみ**（他の writer・rename・削除を OS が拒否）。開いた後に reparse 属性と別名を再確認。
- `probe`: `FILE_READ_ATTRIBUTES` のみ（共有違反の対象外）で開いて属性 tag を読む。
- dir の flush は no-op（dir handle を書込み権限で開かないため。NTFS は metadata を journal する。停電時の耐久性は未検証、§11）。

## 5. 能力表・脅威モデル

競合モデルは E-C1／C2c と同じく「同じユーザーの別 process が任意の時点で名前の変更・差替え・link 作成を行う」。管理者権限・kernel driver・backup 特権による共有モード無視は対象外。

| 性質 | Linux | macOS | Windows（NTFS） |
|---|---|---|---|
| 1 要素ずつの handle 相対 lookup、no-follow | Enforced | Enforced | Enforced |
| 保持中の祖先の rename／削除 | **DetectedOnly**（`verify_reachable` の時点観測） | DetectedOnly | **Enforced**（DELETE 共有なし。既に DELETE を持つ handle があれば保持自体が `busy`） |
| 保持中の dir の種別不変（reparse 化等） | Enforced（inode の種別は変わらない） | Enforced | **DetectedOnly**（空の保持 dir の junction 化は共有モードで防げない。`verify_held()` で handle 越しに再確認） |
| dir の flush（`Dir::sync`） | Enforced（fsync） | Enforced | **NotProvided**（no-op。能力 `dir_flush` で明示） |
| 新規 dir の handle | ReopenedByName（空 dir への差替えは検出不能） | 同左 | **Atomic** |
| 読取り中の変更 | DetectedOnly（前後 fstat・バイト数） | DetectedOnly | **Enforced**（共有 READ のみ）＋同じ検出 |
| mount／volume 越え（push 時） | DetectedOnly（st_dev＋mount id、5.8+） | st_dev のみ（mount id なし→`not_provided`） | Enforced（mount point は reparse として拒否＋volume serial） |
| リモート volume | DetectedOnly（f_type。FUSE は remote 扱い） | DetectedOnly（MNT_LOCAL） | Enforced（device characteristics） |
| hardlink 読取り拒否 | link 数 ≠ 1 を拒否 | 同左 | 同左 |
| 名前の別名（8.3・大小文字・末尾ドット・ADS・device） | 名前規則で拒否 | 名前規則で拒否（Unicode 正規化差は衝突として `already_exists`） | 名前規則＋最終名照合で `name_aliased` |

### 5.1 E-C1（Python POSIX）との比較（Linux）

同等: root からの `O_NOFOLLOW` walk と保持、`O_EXCL|O_NOFOLLOW` 作成、link 数 1、FIFO 非ブロック、1 回読取りと前後 fstat、mkdir 後の所有・空確認、名前による再 walk での位置確認、削除しないこと。
上回る点: mount id による bind mount の検出（E-C1 §11 の「下位フォルダを bind mount した別の場所は検出できない」を `push_existing` で `crosses_volume` にできる。試験 `bind_mount_below_leaf_is_refused_by_mount_id`）。
残る点（POSIX の能力不足。Rust 化では解消しない）: 祖先の rename は防げない（書込みは保持した dir に入り、名前の到達性は時点観測）、mkdir と open の間の空 dir 差替え、Git 判定の時点性。

### 5.2 Windows で残る点・未検証点

- 保持していない子孫（例えば `push_existing` していない兄弟）は保護されない。保護したいものは必ず chain に入れる。
- 保持中の**空の** leaf dir を他 process が `FSCTL_SET_REPARSE_POINT` で junction に変える操作は共有モードでは防げない（`FILE_WRITE_ATTRIBUTES` は共有検査の対象外）。
  能力 `held_kind_stable = detected_only` として報告し、`DirChain::verify_held()`（全保持 handle の種別・identity を handle 越しに再確認）を書込み後に呼ぶ。
  E-C2b では新規 dir に最初に marker を書いて空でなくする。保持 handle からの相対 open が reparse を再評価しない想定は **VM で未検証**（引継ぎ W-14）。
- ACL 継承・`FILE_SHARE_*` の実効性・8.3・SUBST・ネットワーク drive・FAT/ReFS・OneDrive・長パス・別 process 競合は GitHub Windows runner の自動試験で一部、残りは VM（引継ぎ文書）で確認する。runner の成功を VM 受入とみなさない。

## 6. Git checkout の扱い

- 現行（E-C1）は `git check-ignore --no-index` を固定した記述子から呼ぶ（名前ベース・時点判定、gitignore で除外されていれば許可）。
- core には policy を持たせず、**観測の primitive `find_in_ancestors(".git")`** だけを置く（`.git` の dir／file（worktree・submodule）／link を辿らずに検出、保持した各祖先で handle 相対）。
- 比較: (a) 現行の gitignore 判定を Python に残す（互換性は高いが、規則の解釈・`GIT_DIR` 等の環境・判定後の変更に依存）、(b) **保持した祖先のどこかに `.git` があれば全拒否**（単純で handle 相対、ただし checkout 内の ignored 出力先を使う既存利用者には後退）。
- 推奨: E-C2b の Windows 初期解禁は (b) の全拒否から始め（判定の根拠が単純で全 OS 同一）、Linux は既存 (a) を維持する。どちらを製品 policy にするかは**オーナー判断**。
  判定後に `.git` が作られる・持ち込まれる競合はどちらでも時点観測であり、Windows でも子孫の作成は共有モードで止まらない。アプリ固有の Git policy・source 証跡・DB・レンダラは Python 側に残す。

## 7. Python 接続・配布

- binding は 230 行、全メソッドが「引数変換 → `Python::detach`（GIL 解放）→ core 呼出し → 固定コードの `SafeFsError(code, os_code)`」。例外メッセージに名前・パスを含めない（試験で確認）。
- handle 寿命: `DirChain` は context manager／`close()`、参照が消えれば Rust の Drop で全 handle を閉じる（Linux で `/proc/self/fd` の増減を試験）。閉じた後の呼出しは `closed`。
- 書込みは `bytes` だけを受ける（`bytearray` 等の可変 buffer は受けないため、GIL 解放中に内容が変わらない）。読取りは Rust の `Vec` から `bytes` へ 1 回コピー（32 MiB で RSS 約 +64 MiB、§8）。
  E-C2b で必要なら `PyBytes::new_with` で直接読み込み、コピーを 1 回減らせる。
- 長時間 I/O 中も別 Python thread が進むことを 128 MiB 書込みで確認。
- 配布方式の比較:

| 方式 | 内容 | 評価 |
|---|---|---|
| **A. 内部 abi3 wheel（推奨）** | OS／arch ごとに 1 wheel（`cp311-abi3`）。3.11・3.12 で同じ wheel | 実証済み（Linux）。toolchain は build 時のみ |
| B. 製品 wheel に同梱 | Hatchling を maturin 等へ変更、または hatch build hook | 製品 wheel が OS 別になり、`uv.lock`・既存 wheel 試験への影響大。不推奨 |
| C. 独立 helper 実行ファイル | core を CLI 化し subprocess で呼ぶ | GIL・ABI 問題はないが、handle をプロセス境界で保持できず API が粗くなる。比較対象として保留 |

- 製品組込み（E-C2b、未実施）: `gui` extra の実行環境に内部 wheel を入れ、PyInstaller onedir は import された `.pyd` を収集する（hidden import 指定が要る場合あり）。
  portable／installer は同じ onedir を使う。推論 venv（profile venv）には入れない。`uv.lock` への取り込み方（ローカル wheel の path 依存か内部 index か）は要決定。
- **署名**: 新しい native DLL（`.pyd`）は Smart App Control／WDAC（#7）の対象になる。署名方針は配布側の既存課題と合わせてオーナー判断（未検証）。
- manylinux: この環境の build は `manylinux_2_34`。配布範囲によっては maturin の manylinux コンテナ／zig で古い glibc を対象にする（E-C2b）。

## 8. 実測（Cloud Linux、4 vCPU、ext4、Rust 1.97.0）

| 項目 | 値 |
|---|---|
| wheel の release build（依存 download 済み、target 空から、maturin 経由） | 17.3 秒 |
| 完全 cold（空の `CARGO_HOME`・target、download 込み、cargo release build） | 15.3 秒（registry 49 MB、target 83 MB） |
| core 1 ファイル変更後の再 build（warm） | 5.2 秒 |
| wheel サイズ／展開後 | 245 KB／520 KB（うち `.abi3.so` 483 KiB、`strip`・thin LTO） |
| `.so` の動的依存 | `libc.so.6`、`libgcc_s.so.1` のみ |
| 32 MiB の上限付き読取り（binding 経由） | 0.036 秒（3.11・3.12） |
| 128 MiB の排他作成＋書込み＋fsync | 0.22 秒（3.11）／0.27 秒（3.12） |
| 上記ベンチの RSS 増 | 約 +64 MiB（読取り 32 MiB × Rust と Python の 2 copy） |
| core の行数 | src 約 1,940 行（うち Windows 604、POSIX 329）、試験 約 1,300 行 |

参考目標（warm 追加 build 3 分以内、native 10 MiB 以内）は満たす。CI の wall／runner-minutes とキャッシュは §9.2。

## 9. 試験と証拠

### 9.1 Cloud Linux（本セッション、kernel 6.18、ext4）

- `cargo test -p utteran-safe-fs`（root、レビュー修正後）: 単体 4＋`basic` 18（+1 は子 process 用 helper で ignored）＋`unix_races` 7 = **29 PASS**、ignored 3。
- root で `--ignored bind_mount_below_leaf_is_refused_by_mount_id`: PASS。
- 非 root（`nobody`）で `unix_races --include-ignored`: `permission_denied_is_reported` を含め 8 PASS（bind mount は root 必須のため FAIL＝想定。root 実行で PASS）、`basic --include-ignored` 17 PASS。
- `cargo clippy --all-targets -D warnings` を `x86_64-unknown-linux-gnu`／`x86_64-pc-windows-msvc`／`aarch64-apple-darwin` の 3 target で PASS（Windows・macOS は型検査のみ、実行は CI）。
- wheel を新規 venv（3.11.18／3.12.3）へ install し、checkout 外から `test_binding.py` 7 PASS・1 skip（Windows 専用）、`PATH` から cargo／rustc を外して import・実行可能。

試験の内訳（OS 共通）: 正常読取り・作成・flush と同一バイト照合、既存 file／dir と同名の作成拒否と内容・一覧不変、大小文字衝突、上限超過、通常ファイル以外、hardlink、存在しない要素、file を経由する walk、
不正 root 形式、Git marker（dir／file）の検出、書込み途中の失敗注入（部分ファイルが残り削除しない、エラーが持続）、読取り中の変更（Windows は writer 拒否、POSIX は検出）、
**別 process を書込み途中で SIGKILL／TerminateProcess**（部分ファイルは残り、他に何も作られず、handle が解放される）。
POSIX: symlink（file・dir・dangling）を辿らない、FIFO 非ブロック、保持後の祖先 rename＋symlink 差替え（書込みは保持 dir、差替え先は不変、`verify_reachable`＝`unreachable`）、
mkdir 直後の差替え（link→`claim_lost`、非空 dir→`claim_lost`、**空 dir→検出不能（残留として成功を記録）**）、読取り中の伸長・短縮、権限拒否（非 root）、bind mount（root）。
競合はすべて `test-hooks` の同期点で決定的に発生させ、試行回数には依存しない。

### 9.2 GitHub Actions（PoC 専用 workflow）

`native-poc.yml` は `native/**` と自身の変更時だけ動き、製品の `Full product quality gate`（18 job ID）には関与しない。ubuntu／windows／macos の各 runner で
fmt・clippy・core 試験（Windows は `windows_only.rs` を実行）・権限依存試験（POSIX）・wheel build・3.11／3.12 の新規 venv 試験・ベンチ、別 job で cargo-deny。
キャッシュは使わず cold の実測とする（推奨キャッシュ key: OS・arch・`rust-toolchain.toml`・`Cargo.lock` の hash、対象は `~/.cargo/registry` と `native/target`）。
**実行結果・所要時間・NOT_RUN 項目は Draft PR 本文に記録する。** Windows runner の成功は VM 受入の代用にしない。
なお `.github/` の変更は `ci-historical.yml` の UI-1 spike・prototype 検査も起動する（既存の判定規則どおり）。

### 9.3 Windows 11 VM

未実施（NOT_RUN）。手順・期待結果・fixture 範囲は [e-c2a-windows-vm-handoff.md](e-c2a-windows-vm-handoff.md)。

### 9.4 macOS

設計・型検査（Cloud Linux からの cross clippy）と macos runner での実行を分けて記録する。実機受入は対象外（NOT_RUN）。

## 10. 独立レビュー（サブエージェント、2026-11-25、初版コミット対象）

安全性と依存・配布境界を優先して依頼。**P0 なし**。Windows コードは読解のみ（実行なし）。

| ID | 指摘 | 対応 |
|---|---|---|
| P1-1 | `\\?\C:\` は per-logon device map を通り、同じユーザーが `subst`／`DefineDosDevice` で別 volume の root に向けられる（`ensure_volume_root` は通る） | root を `\\?\GLOBALROOT\GLOBAL??\X:\` で開く。per-logon の letter は `invalid_root`。回帰 `per_logon_drive_mapping_is_not_followed`（Windows） |
| P2-1 | Linux のリモート判定が deny-list で未知の FS（CephFS・Lustre 等）を local 扱い | leaf の FS を allow-list で判定し、それ以外は `unsupported_file_system`。回帰 `unlisted_file_system_is_refused`（procfs） |
| P2-2 | Windows の junction／symlink／subst 試験が前提欠如時に NOT_RUN で PASS になる。`mklink` に `\\?\` 付きパス | CI で `SAFE_FS_REQUIRE=junction,symlink,subst` を設定し、欠如時は FAIL。cmd へは接頭辞を外して渡す |
| P2-3 | `--ignored --exact` が改名で 0 件実行のまま成功する | `test result: ok. 1 passed` を必須化 |
| P2-4 | 空の保持 dir の reparse 化を防げないのに `ancestors_pinned: Enforced` のみ | 能力 `held_kind_stable`（Windows `detected_only`）を追加、`verify_held()` を追加、`ancestors_pinned` を rename／削除に限定と明記 |
| P2-5 | POSIX の読取り変更検出は heuristic、文書が保証に読める | 文書と API doc に digest 照合が必要と明記（core に hash 依存は追加しない） |
| P2-6 | binding に `sync`・`is_empty`・読取り metadata がない。Windows の `sync` が無言の no-op | binding に `sync`・`is_empty`・`verify_held`・`read_file_with_metadata` を追加、能力 `dir_flush`（Windows `not_provided`） |
| P3 | 情報 NTSTATUS で handle が漏れる | `NT_SUCCESS`（`st >= 0`）で handle を所有 |
| P3 | VSMB 共有を拒否していない | `FILE_REMOTE_DEVICE_VSMB` を拒否 |
| P3 | 既存 Windows 要素にも UTF-8 255 バイト制限 | 既存要素は UTF-16 255 単位だけで判定（作成名は移植可能規則のまま） |
| P3 | 同じ `Dir` で `is_empty` を並行に呼ぶと列挙位置を共有 | doc に明記 |
| P3 | binding の `max_bytes` が無制限で巨大な確保になり得る | binding で 1 GiB を上限（超過は `too_large`） |
| P3 | CI で binding の clippy と `test-hooks` 非混入の確認がない | 両方を追加 |
| P3 | GIL 試験が偶然で通り得る | 呼出しの内側（両端から 10 ms 離れた区間）に別 thread の時刻記録があることを要求 |
| P3 | POSIX の読取りが型確認前に open する | 文書化（§4.1）。`O_PATH` 経由は Linux 専用のため PoC では採らない |
| P3 | 版を上げていない（AGENTS.md） | **オーナー指示（製品版据置）を優先**し変更しない |
| 情報 | レビュー中の `uv run` が gitignore 済みの `.venv` を作成 | 追跡対象外。削除は行っていない |

確認済みとされた点: POSIX の全 lookup が単一要素 `openat(O_NOFOLLOW)`、`O_EXCL` 作成と dangling link、mkdir 競合の検出と空 dir 残留の明記、mount id による bind mount 拒否、
Windows の全 `NtCreateFile` が RootDirectory＋`FILE_OPEN_REPARSE_POINT`、`FILE_CREATE` の handle 返却、名前規則と最終名照合による 8.3・ADS・末尾ドット・device 名の拒否、
共有モードと DELETE の関係、`windows.rs` の unsafe（UNICODE_STRING 長、buffer 寿命・整列、可変長レコードの境界検査、handle の 1 回所有）、公開 API に生 handle・上書き・削除がないこと、
binding の `detach` が `Send` データのみを借用すること、`test-hooks` が binding に入らないこと、workflow が `ci.yml`（18 ID）と独立で権限 read-only・SHA 固定であること。

## 11. 未解決事項・オーナー判断

1. 採用方式（自前薄層＋rustix＋windows-sys）、配布方式（内部 abi3 wheel）、CI の常設可否の承認。
2. Git policy（Windows 初期は全拒否、Linux は既存判定）の承認（§6）。
3. 新しい native DLL の署名と SAC／WDAC（#7）との関係。
4. VM 受入の範囲と BLOCKED 条件（引継ぎ文書 §5）。とくに保持中の空 dir の reparse 化（W-14）、ACL 継承、FAT/ReFS・ネットワーク drive の拒否。
5. Windows の dir flush（停電時の耐久性）と POSIX の fsync 順序は実機未検証。
6. 非 ASCII の大小文字違いを `name_aliased` で拒否する方針（利用者が大小文字の違うパスを入力した場合に拒否される）。
7. ReFS・Dev Drive を初期対象外（`unsupported_file_system`）とする方針。
8. #59（成果物削除）・D3-B1（外部起動）への再利用は分析のみ。Windows の handle に対する削除（`FileDispositionInfoEx`）は同じ RootDirectory 相対 open の上に設計できるが、本 PoC は削除 API を持たず、BLOCKED を解除しない。

## 12. E-C2b（#87）への移行案

1. 本 PoC の承認後、#83 の main 統合と main CI 成功を待つ。最新 main から独立 branch。
2. `utteran_gui/reexport_files.py` の OS 依存部分だけを core の呼出しに置換する adapter を作る（API・SQLite・レンダラ・冪等性・ログ制約は Python に残す）。
   既存 POSIX 試験（`tests/test_ec_reexport.py`）を Python 実装と Rust 実装の両方で通し、一致を回帰基準にする。差がある経路は切替えない。
3. Windows は VM 受入 PASS の項目だけを解禁し、他は `reexport_platform_unsupported` を維持。Git は全拒否から。
4. 内部 wheel を `gui` 実行環境・PyInstaller・portable・installer に組み込み、配布物の受入（Windows VM）を行う。Rust toolchain を利用者・推論環境に要求しない。
5. 両実装が同等と確認でき次第、Python POSIX 実装を回帰比較用の試験側へ移し、長期の二重実装を避ける（削除はオーナー承認後）。
