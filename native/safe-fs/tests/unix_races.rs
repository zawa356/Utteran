//! POSIX-specific behaviour: links, FIFOs, and deterministic races through the
//! `test-hooks` race points. These tests document both what is refused and the
//! residual races POSIX cannot close (named `residual_*`).
#![cfg(unix)]
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use std::os::unix::fs::{PermissionsExt, symlink};

use common::*;
use utteran_safe_fs::{DirChain, ErrorCode, FileKind, hooks};

fn code<T: std::fmt::Debug>(r: utteran_safe_fs::Result<T>) -> ErrorCode {
    r.expect_err("expected an error").code()
}

#[test]
fn symlinks_are_never_followed() {
    let fx = Fixture::new();
    let target = fx.write("target.json", b"secret-target");
    fx.mkdirs("target-dir");
    symlink(&target, fx.path("link.json")).unwrap();
    symlink(fx.path("target-dir"), fx.path("link-dir")).unwrap();
    symlink(fx.path("missing"), fx.path("dangling")).unwrap();
    let chain = fx.chain("");
    let leaf = chain.leaf();
    assert_eq!(
        code(leaf.read_file(&name("link.json"), 100)),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(
        code(leaf.open_dir(&name("link-dir"))),
        ErrorCode::LinkOrReparse
    );
    assert_eq!(
        code(leaf.create_file(&name("link.json"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(leaf.create_file(&name("dangling"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(leaf.create_dir(&name("dangling"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        leaf.probe(&name("link-dir")).unwrap(),
        Some(FileKind::Symlink)
    );
    assert!(!fx.path("missing").exists(), "dangling target not created");
    assert_eq!(std::fs::read(&target).unwrap(), b"secret-target");
    assert!(listing(&fx.path("target-dir")).is_empty());
    let through = path_str(&fx.path("link-dir"));
    assert_eq!(
        code(DirChain::open_absolute(&through)),
        ErrorCode::LinkOrReparse
    );
    let deeper = path_str(&fx.path("link-dir/x"));
    assert_eq!(
        code(DirChain::open_absolute(&deeper)),
        ErrorCode::LinkOrReparse
    );
}

#[test]
fn fifo_is_refused_without_blocking() {
    let fx = Fixture::new();
    let p = fx.path("pipe");
    rustix_mkfifo(&p);
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().read_file(&name("pipe"), 100)),
        ErrorCode::NotARegularFile
    );
}

fn rustix_mkfifo(p: &std::path::Path) {
    let st = std::process::Command::new("mkfifo")
        .arg(p)
        .status()
        .unwrap();
    assert!(st.success());
}

#[test]
fn renamed_ancestor_keeps_writes_in_the_pinned_directory() {
    let fx = Fixture::new();
    fx.mkdirs("out/sub");
    fx.mkdirs("elsewhere");
    let chain = fx.chain("out/sub");
    // Another process of the same user moves the ancestor and plants a link.
    std::fs::rename(fx.path("out"), fx.path("moved")).unwrap();
    symlink(fx.path("elsewhere"), fx.path("out")).unwrap();
    assert_eq!(code(chain.verify_reachable()), ErrorCode::Unreachable);
    let mut f = chain.leaf().create_file(&name("result.txt")).unwrap();
    f.write_all(b"data").unwrap();
    f.finish().unwrap();
    // The write followed the held handle, not the name.
    assert_eq!(
        std::fs::read(fx.path("moved/sub/result.txt")).unwrap(),
        b"data"
    );
    assert!(listing(&fx.path("elsewhere")).is_empty());
}

#[test]
fn mkdir_reopen_race_swapped_with_link_is_claim_lost() {
    let fx = Fixture::new();
    fx.mkdirs("out");
    fx.mkdirs("decoy");
    let chain = fx.chain("out");
    let (out, decoy) = (fx.path("out"), fx.path("decoy"));
    hooks::set(move |p| {
        if p == hooks::Point::AfterMkdirBeforeOpen {
            std::fs::rename(out.join("new"), out.join("new-moved")).unwrap();
            symlink(&decoy, out.join("new")).unwrap();
        }
        true
    });
    let r = chain.leaf().create_dir(&name("new"));
    hooks::clear();
    assert_eq!(code(r), ErrorCode::ClaimLost);
    assert!(listing(&fx.path("decoy")).is_empty());
    // Nothing was deleted: both the moved directory and the link remain.
    assert_eq!(listing(&fx.path("out")), vec!["new", "new-moved"]);
}

#[test]
fn mkdir_reopen_race_swapped_with_nonempty_dir_is_claim_lost() {
    let fx = Fixture::new();
    fx.mkdirs("out");
    let out = fx.path("out");
    hooks::set(move |p| {
        if p == hooks::Point::AfterMkdirBeforeOpen {
            std::fs::rename(out.join("new"), out.join("new-moved")).unwrap();
            std::fs::create_dir(out.join("new")).unwrap();
            std::fs::write(out.join("new/planted"), b"x").unwrap();
        }
        true
    });
    let r = chain_create(&fx);
    hooks::clear();
    assert_eq!(code(r), ErrorCode::ClaimLost);
    assert_eq!(std::fs::read(fx.path("out/new/planted")).unwrap(), b"x");
}

fn chain_create(fx: &Fixture) -> utteran_safe_fs::Result<()> {
    fx.chain("out").leaf().create_dir(&name("new")).map(|_| ())
}

/// Residual (documented): an *empty* directory swapped in between `mkdirat` and the
/// reopen is indistinguishable on POSIX. Windows creates and opens in one call.
#[test]
fn residual_mkdir_reopen_race_swapped_with_empty_dir_is_not_detected() {
    let fx = Fixture::new();
    fx.mkdirs("out");
    let out = fx.path("out");
    let swapped_ino = std::rc::Rc::new(std::cell::Cell::new(0u64));
    let s2 = swapped_ino.clone();
    hooks::set(move |p| {
        if p == hooks::Point::AfterMkdirBeforeOpen {
            use std::os::unix::fs::MetadataExt;
            std::fs::rename(out.join("new"), out.join("new-moved")).unwrap();
            std::fs::create_dir(out.join("new")).unwrap();
            s2.set(std::fs::metadata(out.join("new")).unwrap().ino());
        }
        true
    });
    let chain = fx.chain("out");
    let r = chain.leaf().create_dir(&name("new"));
    hooks::clear();
    let (dir, _) = r.expect("POSIX cannot detect an empty-directory swap");
    assert_eq!(
        dir.metadata().unwrap().identity.file,
        u128::from(swapped_ino.get())
    );
}

#[test]
fn bounded_read_detects_truncation_and_growth() {
    for grow in [true, false] {
        let fx = Fixture::new();
        let p = fx.write("src.json", &pattern(8192));
        let chain = fx.chain("");
        hooks::set(move |pt| {
            if pt == hooks::Point::AfterReadOpen {
                let f = std::fs::OpenOptions::new().write(true).open(&p).unwrap();
                f.set_len(if grow { 9000 } else { 100 }).unwrap();
            }
            true
        });
        let r = chain.leaf().read_file(&name("src.json"), 1 << 20);
        hooks::clear();
        assert_eq!(code(r), ErrorCode::ChangedDuringRead);
    }
}

/// Requires a non-root user (root bypasses DAC). Run with `--ignored` as such a user.
#[test]
#[ignore = "requires a non-root user"]
fn permission_denied_is_reported() {
    assert_ne!(rustix_euid(), 0, "run as a non-root user");
    let fx = Fixture::new();
    let d = fx.mkdirs("locked");
    fx.write("locked/x", b"x");
    fx.write("unreadable", b"x");
    std::fs::set_permissions(
        fx.path("unreadable"),
        std::fs::Permissions::from_mode(0o000),
    )
    .unwrap();
    let chain = fx.chain("");
    std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o000)).unwrap();
    let r1 = chain.leaf().open_dir(&name("locked"));
    let r2 = DirChain::open_absolute(&path_str(&fx.path("locked/sub")));
    let r3 = chain.leaf().read_file(&name("unreadable"), 10);
    std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(code(r1), ErrorCode::PermissionDenied);
    assert_eq!(code(r2), ErrorCode::PermissionDenied);
    assert_eq!(code(r3), ErrorCode::PermissionDenied);
    std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o500)).unwrap();
    let ro = fx.chain("locked");
    let r4 = ro.leaf().create_file(&name("new"));
    std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert_eq!(code(r4), ErrorCode::PermissionDenied);
    assert_eq!(listing(&d), vec!["x"]);
}

fn rustix_euid() -> u32 {
    // Avoid an extra dev-dependency: `id -u`.
    let o = std::process::Command::new("id").arg("-u").output().unwrap();
    String::from_utf8(o.stdout).unwrap().trim().parse().unwrap()
}

/// Requires root (Linux bind mount). Run with `--ignored` as root.
#[cfg(target_os = "linux")]
#[test]
#[ignore = "requires root for mount --bind"]
fn bind_mount_below_leaf_is_refused_by_mount_id() {
    assert_eq!(rustix_euid(), 0);
    let fx = Fixture::new();
    fx.mkdirs("job/private");
    fx.write("job/private/transcript.json", b"{}");
    fx.mkdirs("out/b");
    let st = std::process::Command::new("mount")
        .arg("--bind")
        .arg(fx.path("job/private"))
        .arg(fx.path("out/b"))
        .status()
        .unwrap();
    assert!(st.success());
    struct Umount(std::path::PathBuf);
    impl Drop for Umount {
        fn drop(&mut self) {
            let _ = std::process::Command::new("umount").arg(&self.0).status();
        }
    }
    let _guard = Umount(fx.path("out/b"));
    let mut chain = fx.chain("out");
    let leaf_meta = chain.leaf().metadata().unwrap();
    assert!(leaf_meta.mount_id.is_some(), "statx STATX_MNT_ID available");
    let r = chain.push_existing(&name("b"));
    assert_eq!(code(r), ErrorCode::CrossesVolume);
    // The generic Dir API applies the same boundary (owner review P2-1).
    assert_eq!(
        code(chain.leaf().open_dir(&name("b"))),
        ErrorCode::CrossesVolume
    );
    // Residual (documented): walking the bind target by its own absolute path is a
    // fresh walk, so the alias is not recognisable as the job directory by this crate.
    // Same st_dev: only the mount id tells them apart.
    let bind = DirChain::open_absolute(&path_str(&fx.path("out/b"))).unwrap();
    let bm = bind.leaf().metadata().unwrap();
    assert_eq!(bm.identity.volume, leaf_meta.identity.volume);
    assert_ne!(bm.mount_id, leaf_meta.mount_id);
}

/// Requires root. A regular file bind-mounted over a name is refused for reading.
#[cfg(target_os = "linux")]
#[test]
#[ignore = "requires root for mount --bind"]
fn file_bind_mount_is_refused_for_reading() {
    assert_eq!(rustix_euid(), 0);
    let fx = Fixture::new();
    fx.mkdirs("job");
    fx.mkdirs("out");
    fx.write("job/private.json", b"{\"secret\":1}");
    fx.write("out/src.json", b"{}");
    let st = std::process::Command::new("mount")
        .arg("--bind")
        .arg(fx.path("job/private.json"))
        .arg(fx.path("out/src.json"))
        .status()
        .unwrap();
    assert!(st.success());
    struct Umount(std::path::PathBuf);
    impl Drop for Umount {
        fn drop(&mut self) {
            let _ = std::process::Command::new("umount").arg(&self.0).status();
        }
    }
    let _guard = Umount(fx.path("out/src.json"));
    let chain = fx.chain("out");
    assert_eq!(
        code(chain.leaf().read_file(&name("src.json"), 100)),
        ErrorCode::CrossesVolume
    );
}
