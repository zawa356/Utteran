//! Shared synthetic fixtures. Every test works only inside its own fresh temp
//! directory (removed on drop by `tempfile`); nothing outside it is touched.
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use utteran_safe_fs::{DirChain, Name};

pub struct Fixture {
    _tmp: tempfile::TempDir,
    /// Canonical path of the fixture root (no links, long names on Windows).
    pub root: PathBuf,
}

impl Fixture {
    pub fn new() -> Self {
        // SAFE_FS_TEST_ROOT lets a VM place fixtures on a chosen local NTFS dir.
        let tmp = match std::env::var_os("SAFE_FS_TEST_ROOT") {
            Some(base) => tempfile::Builder::new()
                .prefix("safefs-")
                .tempdir_in(base)
                .unwrap(),
            None => tempfile::Builder::new()
                .prefix("safefs-")
                .tempdir()
                .unwrap(),
        };
        let root = std::fs::canonicalize(tmp.path()).unwrap();
        Self { _tmp: tmp, root }
    }

    pub fn path(&self, rel: &str) -> PathBuf {
        let mut p = self.root.clone();
        for c in rel.split('/').filter(|c| !c.is_empty()) {
            p.push(c);
        }
        p
    }

    pub fn mkdirs(&self, rel: &str) -> PathBuf {
        let p = self.path(rel);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    pub fn write(&self, rel: &str, data: &[u8]) -> PathBuf {
        let p = self.path(rel);
        std::fs::write(&p, data).unwrap();
        p
    }

    pub fn chain(&self, rel: &str) -> DirChain {
        DirChain::open_absolute(&path_str(&self.path(rel))).unwrap()
    }
}

/// Path as the crate expects it (`\\?\C:\...` from canonicalize is accepted).
pub fn path_str(p: &Path) -> String {
    p.to_str().unwrap().to_owned()
}

pub fn name(s: &str) -> Name {
    Name::new(s).unwrap()
}

pub fn sha256(data: &[u8]) -> [u8; 32] {
    Sha256::digest(data).into()
}

pub fn file_hash(p: &Path) -> [u8; 32] {
    sha256(&std::fs::read(p).unwrap())
}

/// Deterministic synthetic payload (no real user data).
pub fn pattern(len: usize) -> Vec<u8> {
    (0..len)
        .map(|i| (i.wrapping_mul(31) ^ (i >> 7)) as u8)
        .collect()
}

/// Sorted listing of a directory, for "nothing else changed" assertions.
pub fn listing(p: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(p)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    v.sort();
    v
}
