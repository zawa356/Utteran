//! Platform-neutral behaviour. Runs on Linux, macOS and Windows.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use common::*;
use utteran_safe_fs::{DirChain, DirCreation, ErrorCode, FileKind, Guarantee, capabilities, hooks};

fn code<T: std::fmt::Debug>(r: utteran_safe_fs::Result<T>) -> ErrorCode {
    r.expect_err("expected an error").code()
}

#[test]
fn capabilities_are_reported_per_platform() {
    let c = capabilities();
    assert!(c.handle_relative_lookup && c.no_follow);
    if cfg!(windows) {
        assert_eq!(c.platform, "windows");
        assert_eq!(c.ancestors_pinned, Guarantee::Enforced);
        assert_eq!(c.dir_creation, DirCreation::Atomic);
    } else {
        assert_eq!(c.ancestors_pinned, Guarantee::DetectedOnly);
        assert_eq!(c.dir_creation, DirCreation::ReopenedByName);
    }
    println!("capabilities: {c:?}");
}

#[test]
fn walk_holds_every_component_with_identity() {
    let fx = Fixture::new();
    fx.mkdirs("a/b/c");
    let chain = fx.chain("a/b/c");
    let comps = fx.path("a/b/c").components().count();
    // Windows `\\?\C:\` yields Prefix + RootDir components for one root handle.
    let expected = if cfg!(windows) { comps - 1 } else { comps };
    assert_eq!(chain.depth(), expected);
    let ids = chain.identities();
    assert_eq!(
        ids.last().copied(),
        Some(chain.leaf().metadata().unwrap().identity)
    );
    let mut dedup = ids.clone();
    dedup.sort_by_key(|i| (i.volume, i.file));
    dedup.dedup();
    assert_eq!(
        dedup.len(),
        ids.len(),
        "every held element is a distinct object"
    );
    chain.verify_reachable().unwrap();
    println!("fs_kind: {}", chain.fs_kind().as_str());
}

#[test]
fn malformed_roots_are_refused() {
    let fx = Fixture::new();
    fx.mkdirs("a/b");
    let base = path_str(&fx.root);
    let sep = if cfg!(windows) { '\\' } else { '/' };
    let mut bad = vec![
        String::new(),
        "relative".to_owned(),
        format!("{base}{sep}a{sep}..{sep}a"),
        format!("{base}{sep}a{sep}.{sep}b"),
        format!("{base}{sep}a{sep}{sep}b"),
        format!("{base}{sep}a{sep}"),
        format!("{base}{sep}a\0b"),
    ];
    if cfg!(windows) {
        bad.extend(
            [
                r"\\server\share\x",
                r"\\?\UNC\server\share\x",
                r"\\.\C:\x",
                r"\\?\GLOBALROOT\Device\HarddiskVolume1\x",
                r"C:x",
                r"\x",
                r"C:/x",
            ]
            .map(str::to_owned),
        );
        bad.push(format!("{base}\\a:stream"));
        bad.push(format!("{base}\\a."));
        bad.push(format!("{base}\\CON"));
    } else {
        bad.push("//x".to_owned());
    }
    for p in bad {
        assert_eq!(
            code(DirChain::open_absolute(&p)),
            ErrorCode::InvalidRoot,
            "must refuse a malformed root form"
        );
    }
}

#[test]
fn missing_component_is_not_found() {
    let fx = Fixture::new();
    let p = path_str(&fx.path("nope/deeper"));
    assert_eq!(code(DirChain::open_absolute(&p)), ErrorCode::NotFound);
}

#[test]
fn walk_through_a_file_is_refused() {
    let fx = Fixture::new();
    fx.write("f", b"x");
    let p = path_str(&fx.path("f"));
    assert_eq!(code(DirChain::open_absolute(&p)), ErrorCode::NotADirectory);
}

#[test]
fn create_write_flush_and_read_back_same_bytes() {
    let fx = Fixture::new();
    let chain = fx.chain("");
    let data = pattern(3 * 1024 * 1024 + 17);
    let mut f = chain.leaf().create_file(&name("out.srt")).unwrap();
    f.write_all(&data).unwrap();
    let m = f.finish().unwrap();
    assert_eq!(m.size, data.len() as u64);
    assert_eq!(m.link_count, 1);
    assert_eq!(m.kind, FileKind::File);
    chain.leaf().sync().unwrap();
    let r = chain
        .leaf()
        .read_file(&name("out.srt"), data.len() as u64)
        .unwrap();
    assert_eq!(sha256(&r.bytes), sha256(&data));
    assert_eq!(r.metadata.identity, m.identity);
    assert_eq!(file_hash(&fx.path("out.srt")), sha256(&data));
}

#[test]
fn exclusive_create_never_touches_existing_entries() {
    let fx = Fixture::new();
    let f = fx.write("existing.txt", b"keep me");
    fx.mkdirs("existing-dir");
    fx.write("existing-dir/inner", b"inner");
    let before_hash = file_hash(&f);
    let before_inner = file_hash(&fx.path("existing-dir/inner"));
    let chain = fx.chain("");
    let leaf = chain.leaf();
    assert_eq!(
        code(leaf.create_file(&name("existing.txt"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(leaf.create_file(&name("existing-dir"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(leaf.create_dir(&name("existing-dir"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        code(leaf.create_dir(&name("existing.txt"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(file_hash(&f), before_hash);
    assert_eq!(file_hash(&fx.path("existing-dir/inner")), before_inner);
    assert_eq!(listing(&fx.path("existing-dir")), vec!["inner"]);
}

#[test]
fn case_variant_collision_on_case_insensitive_fs() {
    let fx = Fixture::new();
    fx.write("Report.SRT", b"x");
    let chain = fx.chain("");
    let r = chain.leaf().create_file(&name("report.srt"));
    // ext4 is case-sensitive; NTFS/APFS (default) are not. Either way the existing
    // file must be unchanged.
    match r {
        Ok(f) => {
            drop(f);
            let case_sensitive_fs = cfg!(target_os = "linux");
            assert!(case_sensitive_fs, "case-insensitive FS must refuse");
        }
        Err(e) => assert_eq!(e.code(), ErrorCode::AlreadyExists),
    }
    assert_eq!(std::fs::read(fx.path("Report.SRT")).unwrap(), b"x");
}

#[test]
fn read_limit_and_regular_file_checks() {
    let fx = Fixture::new();
    fx.write("ten", b"0123456789");
    fx.mkdirs("dir");
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().read_file(&name("ten"), 9)),
        ErrorCode::TooLarge
    );
    assert_eq!(
        chain.leaf().read_file(&name("ten"), 10).unwrap().bytes,
        b"0123456789"
    );
    let e = code(chain.leaf().read_file(&name("dir"), 10));
    assert!(
        matches!(e, ErrorCode::NotARegularFile | ErrorCode::PermissionDenied),
        "{e:?}"
    );
    assert_eq!(
        code(chain.leaf().read_file(&name("absent"), 10)),
        ErrorCode::NotFound
    );
}

#[test]
fn hard_linked_file_is_refused_for_reading() {
    let fx = Fixture::new();
    let a = fx.write("a.json", b"{}");
    std::fs::hard_link(&a, fx.path("b.json")).unwrap();
    let chain = fx.chain("");
    assert_eq!(
        code(chain.leaf().read_file(&name("a.json"), 100)),
        ErrorCode::HardLinked
    );
    assert_eq!(
        code(chain.leaf().read_file(&name("b.json"), 100)),
        ErrorCode::HardLinked
    );
}

#[test]
fn push_new_then_write_and_verify_location() {
    let fx = Fixture::new();
    fx.mkdirs("out");
    let mut chain = fx.chain("out");
    let how = chain.push_new(&name("job_reexport_1")).unwrap();
    assert_eq!(how, capabilities().dir_creation);
    assert!(chain.leaf().is_empty().unwrap());
    let mut f = chain
        .leaf()
        .create_file(&name(".utteran-reexport"))
        .unwrap();
    f.write_all(b"marker").unwrap();
    f.finish().unwrap();
    assert!(!chain.leaf().is_empty().unwrap());
    chain.verify_reachable().unwrap();
    assert_eq!(
        code(chain.push_new(&name(".utteran-reexport"))),
        ErrorCode::AlreadyExists
    );
    assert_eq!(
        std::fs::read(fx.path("out/job_reexport_1/.utteran-reexport")).unwrap(),
        b"marker"
    );
}

#[test]
fn push_existing_and_probe() {
    let fx = Fixture::new();
    fx.mkdirs("a/b");
    fx.write("a/file", b"x");
    let mut chain = fx.chain("a");
    assert_eq!(
        chain.leaf().probe(&name("b")).unwrap(),
        Some(FileKind::Directory)
    );
    assert_eq!(
        chain.leaf().probe(&name("file")).unwrap(),
        Some(FileKind::File)
    );
    assert_eq!(chain.leaf().probe(&name("none")).unwrap(), None);
    assert_eq!(
        code(chain.push_existing(&name("file"))),
        ErrorCode::NotADirectory
    );
    chain.push_existing(&name("b")).unwrap();
    chain.verify_reachable().unwrap();
}

#[test]
fn git_marker_in_any_ancestor_is_found_without_following() {
    for marker_is_dir in [true, false] {
        let fx = Fixture::new();
        if marker_is_dir {
            fx.mkdirs("repo/.git");
        } else {
            fx.mkdirs("repo");
            // Linked worktrees and submodules use a `.git` *file*.
            fx.write("repo/.git", b"gitdir: elsewhere\n");
        }
        fx.mkdirs("repo/sub/out");
        let chain = fx.chain("repo/sub/out");
        let found = chain.find_in_ancestors(&name(".git")).unwrap();
        let (idx, kind) = found.expect("marker must be found");
        assert_eq!(idx, chain.depth() - 3, "found at `repo`");
        assert_eq!(
            kind,
            if marker_is_dir {
                FileKind::Directory
            } else {
                FileKind::File
            }
        );
        let outside = fx.chain("");
        assert_eq!(
            outside
                .find_in_ancestors(&name(".git"))
                .unwrap()
                .map(|x| x.0),
            None
        );
    }
}

#[test]
fn injected_write_failure_keeps_partial_and_deletes_nothing() {
    let fx = Fixture::new();
    fx.write("other", b"untouched");
    let chain = fx.chain("");
    hooks::set(|p| !matches!(p, hooks::Point::BeforeWriteChunk(n) if n >= 1 << 20));
    let mut f = chain.leaf().create_file(&name("partial.txt")).unwrap();
    let e = f.write_all(&pattern(3 << 20)).unwrap_err();
    hooks::clear();
    assert_eq!(e.code(), ErrorCode::WriteFailed);
    assert_eq!(f.written(), 1 << 20);
    assert_eq!(code(f.write_all(b"more")), ErrorCode::WriteFailed, "sticky");
    assert_eq!(code(f.finish()), ErrorCode::WriteFailed);
    assert_eq!(
        std::fs::metadata(fx.path("partial.txt")).unwrap().len(),
        1 << 20
    );
    assert_eq!(listing(&fx.root), vec!["other", "partial.txt"]);
    assert_eq!(std::fs::read(fx.path("other")).unwrap(), b"untouched");
}

#[test]
fn concurrent_modification_during_read_is_refused_or_detected() {
    let fx = Fixture::new();
    let p = fx.write("src.json", &pattern(4096));
    let chain = fx.chain("");
    let p2 = p.clone();
    let writer_result = std::rc::Rc::new(std::cell::RefCell::new(None));
    let wr = writer_result.clone();
    hooks::set(move |pt| {
        if pt == hooks::Point::AfterReadOpen {
            use std::io::Write;
            let r = std::fs::OpenOptions::new()
                .append(true)
                .open(&p2)
                .and_then(|mut f| f.write_all(b"tamper"));
            *wr.borrow_mut() = Some(r.map_err(|e| e.raw_os_error()));
        }
        true
    });
    let r = chain.leaf().read_file(&name("src.json"), 1 << 20);
    hooks::clear();
    let writer = (*writer_result.borrow()).expect("hook ran");
    if cfg!(windows) {
        // FILE_SHARE_READ only: the OS refuses the writer (ERROR_SHARING_VIOLATION).
        assert_eq!(writer, Err(Some(32)));
        assert_eq!(sha256(&r.unwrap().bytes), sha256(&pattern(4096)));
    } else {
        // POSIX cannot refuse the writer; the change is detected.
        assert_eq!(writer, Ok(()));
        assert_eq!(code(r), ErrorCode::ChangedDuringRead);
    }
}

/// Child half of `kill_mid_write_*`: runs only when spawned by that test.
#[test]
#[ignore = "helper process for kill_mid_write_releases_handles_and_keeps_partial"]
fn child_writer_entry() {
    use std::io::Write;
    let Some(dir) = std::env::var_os("SAFE_FS_CHILD_DIR") else {
        return;
    };
    let chain = DirChain::open_absolute(dir.to_str().unwrap()).unwrap();
    let mut f = chain.leaf().create_file(&name("partial.bin")).unwrap();
    f.write_all(&pattern(2 << 20)).unwrap();
    let mut out = std::io::stdout();
    writeln!(out, "SAFE_FS_CHILD_READY").unwrap();
    out.flush().unwrap();
    loop {
        std::thread::sleep(std::time::Duration::from_secs(60));
    }
}

#[test]
fn kill_mid_write_releases_handles_and_keeps_partial() {
    use std::io::{BufRead, BufReader};
    let fx = Fixture::new();
    let dir = fx.mkdirs("job");
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "child_writer_entry",
            "--include-ignored",
            "--nocapture",
        ])
        .env("SAFE_FS_CHILD_DIR", &dir)
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let mut ready = false;
    for line in BufReader::new(stdout).lines() {
        if line.unwrap().contains("SAFE_FS_CHILD_READY") {
            ready = true;
            break;
        }
    }
    assert!(ready, "child reached the barrier");
    child.kill().unwrap(); // SIGKILL / TerminateProcess: no destructors run
    child.wait().unwrap();
    assert_eq!(listing(&dir), vec!["partial.bin"]);
    assert_eq!(
        file_hash(&dir.join("partial.bin")),
        sha256(&pattern(2 << 20))
    );
    // Every handle the child held is released: on Windows the pinned dir can now be
    // renamed (it could not while held).
    std::fs::rename(&dir, fx.path("job-renamed")).unwrap();
}

#[test]
fn verify_held_accepts_unchanged_chain() {
    let fx = Fixture::new();
    fx.mkdirs("out");
    let mut chain = fx.chain("out");
    chain.push_new(&name("job")).unwrap();
    let mut f = chain.leaf().create_file(&name("marker")).unwrap();
    f.write_all(b"m").unwrap();
    f.finish().unwrap();
    chain.verify_held().unwrap();
    let c = capabilities();
    println!(
        "held_kind_stable={} dir_flush={}",
        c.held_kind_stable.as_str(),
        c.dir_flush.as_str()
    );
}

/// Fail closed on file systems outside the allow-list (procfs on Linux).
#[cfg(target_os = "linux")]
#[test]
fn unlisted_file_system_is_refused() {
    assert_eq!(
        code(DirChain::open_absolute("/proc/sys")),
        ErrorCode::UnsupportedFileSystem
    );
}
