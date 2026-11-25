//! Windows-specific behaviour (local NTFS). Compiled on every platform's CI for
//! type-checking via `cargo check --target`, executed only on Windows.
//!
//! Tests that need a facility the runner may not offer (symlink privilege, 8.3
//! names, a FAT volume, a free drive letter) print `NOT_RUN:` with the reason and
//! return early; the evidence table must list them as NOT_RUN, never PASS.
#![cfg(windows)]
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use std::os::windows::fs::OpenOptionsExt;
use std::process::Command;

use common::*;
use utteran_safe_fs::{DirChain, ErrorCode, FileKind, FsKind};

const DELETE: u32 = 0x0001_0000;
const FILE_SHARE_ALL: u32 = 0x7;
const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
const ERROR_SHARING_VIOLATION: i32 = 32;
const ERROR_ACCESS_DENIED: i32 = 5;

fn code<T: std::fmt::Debug>(r: utteran_safe_fs::Result<T>) -> ErrorCode {
    r.expect_err("expected an error").code()
}

fn not_run(why: &str) {
    println!("NOT_RUN: {why}");
}

fn cmd(args: &[&str]) -> bool {
    Command::new("cmd")
        .arg("/C")
        .args(args)
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[test]
fn local_ntfs_root_is_accepted() {
    let fx = Fixture::new();
    let chain = fx.chain("");
    assert_eq!(chain.fs_kind(), FsKind::Ntfs);
    // `C:\...` form (without `\\?\`) is also accepted and reaches the same objects.
    let plain = path_str(&fx.root).trim_start_matches(r"\\?\").to_owned();
    let c2 = DirChain::open_absolute(&plain).unwrap();
    assert_eq!(c2.identities(), chain.identities());
}

#[test]
fn held_ancestors_cannot_be_renamed_or_deleted() {
    let fx = Fixture::new();
    fx.mkdirs("a/b/c");
    let chain = fx.chain("a/b/c");
    for (from, to) in [("a", "a2"), ("a/b", "a/b2"), ("a/b/c", "a/b/c2")] {
        let e = std::fs::rename(fx.path(from), fx.path(to)).unwrap_err();
        assert!(
            matches!(
                e.raw_os_error(),
                Some(ERROR_SHARING_VIOLATION | ERROR_ACCESS_DENIED)
            ),
            "rename of a held ancestor must be refused: {e:?}"
        );
    }
    let e = std::fs::remove_dir(fx.path("a/b/c")).unwrap_err();
    assert!(matches!(
        e.raw_os_error(),
        Some(ERROR_SHARING_VIOLATION | ERROR_ACCESS_DENIED)
    ));
    chain.verify_reachable().unwrap();
    drop(chain);
    std::fs::rename(fx.path("a"), fx.path("a2")).unwrap();
}

#[test]
fn existing_delete_access_handle_blocks_pinning() {
    let fx = Fixture::new();
    fx.mkdirs("a/b");
    let holder = std::fs::OpenOptions::new()
        .access_mode(DELETE)
        .share_mode(FILE_SHARE_ALL)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(fx.path("a"))
        .unwrap();
    let r = DirChain::open_absolute(&path_str(&fx.path("a/b")));
    assert_eq!(code(r), ErrorCode::Busy);
    drop(holder);
    fx.chain("a/b");
}

#[test]
fn open_writer_blocks_read_and_reader_blocks_writer() {
    let fx = Fixture::new();
    let p = fx.write("src.json", b"{}");
    let writer = std::fs::OpenOptions::new()
        .write(true)
        .share_mode(FILE_SHARE_ALL)
        .open(&p)
        .unwrap();
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().read_file(&name("src.json"), 10)),
        ErrorCode::Busy
    );
    drop(writer);
    assert_eq!(
        chain.leaf().read_file(&name("src.json"), 10).unwrap().bytes,
        b"{}"
    );
}

#[test]
fn junctions_are_refused_in_walk_and_as_children() {
    let fx = Fixture::new();
    fx.mkdirs("target");
    fx.write("target/keep", b"keep");
    let j = fx.path("junction");
    if !cmd(&[
        "mklink",
        "/J",
        j.to_str().unwrap(),
        fx.path("target").to_str().unwrap(),
    ]) {
        return not_run("mklink /J failed");
    }
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().open_dir(&name("junction"))),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(
        chain.leaf().probe(&name("junction")).unwrap(),
        Some(FileKind::ReparsePoint)
    );
    assert_eq!(
        code(chain.leaf().create_dir(&name("junction"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(chain.leaf().create_file(&name("junction"))),
        ErrorCode::AlreadyExists
    );
    let through = path_str(&fx.path("junction"));
    assert_eq!(
        code(DirChain::open_absolute(&through)),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(listing(&fx.path("target")), vec!["keep"]);
}

#[test]
fn symlinks_are_refused_when_creatable() {
    let fx = Fixture::new();
    let t = fx.write("t.json", b"target");
    fx.mkdirs("tdir");
    let f = std::os::windows::fs::symlink_file(&t, fx.path("lf.json"));
    let d = std::os::windows::fs::symlink_dir(fx.path("tdir"), fx.path("ld"));
    if f.is_err() || d.is_err() {
        return not_run("symlink creation privilege not held (ERROR_PRIVILEGE_NOT_HELD)");
    }
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().read_file(&name("lf.json"), 100)),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(
        code(chain.leaf().open_dir(&name("ld"))),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(
        code(chain.leaf().create_file(&name("lf.json"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(std::fs::read(&t).unwrap(), b"target");
    assert!(listing(&fx.path("tdir")).is_empty());
}

#[test]
fn short_8dot3_alias_is_refused() {
    let fx = Fixture::new();
    fx.mkdirs("Long Directory Name For Alias");
    let alias = "LONGDI~1";
    if !fx.path(alias).exists() {
        return not_run("8.3 short names disabled on this volume");
    }
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().open_dir(&name(alias))),
        ErrorCode::NameAliased
    );
    let via_alias = path_str(&fx.path(alias));
    assert_eq!(
        code(DirChain::open_absolute(&via_alias)),
        ErrorCode::NameAliased
    );
}

#[test]
fn ascii_case_variant_reaches_the_same_directory() {
    let fx = Fixture::new();
    fx.mkdirs("CaseDir");
    let chain = fx.chain("");
    let a = chain
        .leaf()
        .open_dir(&name("CaseDir"))
        .unwrap()
        .metadata()
        .unwrap();
    let b = chain
        .leaf()
        .open_dir(&name("casedir"))
        .unwrap()
        .metadata()
        .unwrap();
    assert_eq!(a.identity, b.identity);
}

#[test]
fn unicode_and_long_paths_beyond_max_path() {
    let fx = Fixture::new();
    let mut chain = fx.chain("");
    let seg = "長いフォルダ名-segment-0123456789";
    for i in 0..12 {
        chain.push_new(&name(&format!("{seg}-{i:02}"))).unwrap();
    }
    let mut f = chain.leaf().create_file(&name("結果.srt")).unwrap();
    f.write_all(b"1\n").unwrap();
    f.finish().unwrap();
    let mut p = fx.root.clone();
    for i in 0..12 {
        p.push(format!("{seg}-{i:02}"));
    }
    assert!(p.as_os_str().len() > 260, "beyond MAX_PATH");
    let again = DirChain::open_absolute(&path_str(&p)).unwrap();
    assert_eq!(again.identities(), chain.identities());
    chain.verify_reachable().unwrap();
}

#[test]
fn unc_and_device_namespace_roots_are_refused() {
    for p in [
        r"\\localhost\C$\Windows",
        r"\\?\UNC\localhost\C$\Windows",
        r"\\.\C:\Windows",
        r"\\?\Volume{00000000-0000-0000-0000-000000000000}\",
    ] {
        assert_eq!(code(DirChain::open_absolute(p)), ErrorCode::InvalidRoot);
    }
}

#[test]
fn subst_drive_is_refused() {
    let fx = Fixture::new();
    let Some(letter) = ('M'..='Z')
        .rev()
        .find(|l| !std::path::Path::new(&format!("{l}:\\")).exists())
    else {
        return not_run("no free drive letter");
    };
    let drive = format!("{letter}:");
    let target = path_str(&fx.root).trim_start_matches(r"\\?\").to_owned();
    if !cmd(&["subst", &drive, &target]) {
        return not_run("subst unavailable");
    }
    let r = DirChain::open_absolute(&format!("{drive}\\"));
    cmd(&["subst", &drive, "/D"]);
    assert_eq!(code(r), ErrorCode::InvalidRoot);
}

/// Set SAFE_FS_NON_NTFS_ROOT to a directory on a FAT32/exFAT/ReFS volume (e.g. a
/// mounted VHD in the VM) to run this.
#[test]
fn non_ntfs_volume_is_refused() {
    let Ok(p) = std::env::var("SAFE_FS_NON_NTFS_ROOT") else {
        return not_run("SAFE_FS_NON_NTFS_ROOT not set");
    };
    assert_eq!(
        code(DirChain::open_absolute(&p)),
        ErrorCode::UnsupportedFileSystem
    );
}

/// Set SAFE_FS_REMOTE_ROOT to a mapped network drive path (e.g. `Z:\share-dir`).
#[test]
fn mapped_network_drive_is_refused() {
    let Ok(p) = std::env::var("SAFE_FS_REMOTE_ROOT") else {
        return not_run("SAFE_FS_REMOTE_ROOT not set");
    };
    assert_eq!(code(DirChain::open_absolute(&p)), ErrorCode::RemoteVolume);
}

#[test]
fn ads_and_device_names_cannot_be_named() {
    for bad in [
        "f.txt:hidden",
        "f.txt::$DATA",
        "CON",
        "nul.txt",
        "COM1",
        "a.",
        "a ",
    ] {
        assert!(utteran_safe_fs::Name::new(bad).is_err());
    }
}
