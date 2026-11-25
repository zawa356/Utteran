//! POSIX backend on `rustix` (safe wrappers; this module contains no `unsafe`).
//!
//! Every lookup is `openat(parent_fd, single_component, O_NOFOLLOW | ...)`. POSIX
//! cannot stop another process of the same user from renaming a held directory,
//! so ancestor pinning is `DetectedOnly` (see `DirChain::verify_reachable`).

use std::fs::File;
use std::os::fd::{AsFd, OwnedFd};

use rustix::fs::{AtFlags, FileType, Mode, OFlags, openat};
use rustix::io::Errno;

use crate::error::{Error, ErrorCode, Result};
use crate::meta::{Capabilities, DirCreation, FileKind, FsKind, Guarantee, Identity, Metadata};

pub(crate) fn capabilities() -> Capabilities {
    Capabilities {
        platform: if cfg!(target_os = "linux") {
            "linux"
        } else if cfg!(target_os = "macos") {
            "macos"
        } else {
            "other-unix"
        },
        handle_relative_lookup: true,
        no_follow: true,
        ancestors_pinned: Guarantee::DetectedOnly,
        // An inode's type never changes; a rename is covered by `ancestors_pinned`.
        held_kind_stable: Guarantee::Enforced,
        dir_flush: Guarantee::Enforced,
        read_exclusive: Guarantee::DetectedOnly,
        dir_creation: DirCreation::ReopenedByName,
        mount_identity: if mount_id_supported() {
            Guarantee::DetectedOnly
        } else {
            Guarantee::NotProvided
        },
        remote_refused: Guarantee::DetectedOnly,
    }
}

fn map_errno(e: Errno) -> Error {
    let code = match e {
        Errno::NOENT => ErrorCode::NotFound,
        Errno::EXIST => ErrorCode::AlreadyExists,
        Errno::ACCESS | Errno::PERM => ErrorCode::PermissionDenied,
        Errno::LOOP => ErrorCode::LinkOrReparse,
        Errno::NOTDIR => ErrorCode::NotADirectory,
        Errno::ISDIR => ErrorCode::NotARegularFile,
        Errno::NAMETOOLONG | Errno::INVAL => ErrorCode::InvalidName,
        Errno::BUSY | Errno::TXTBSY | Errno::AGAIN => ErrorCode::Busy,
        Errno::XDEV => ErrorCode::CrossesVolume,
        // FreeBSD/macOS variants of "O_NOFOLLOW hit a symlink".
        #[cfg(not(target_os = "linux"))]
        Errno::MLINK => ErrorCode::LinkOrReparse,
        _ => ErrorCode::Io,
    };
    Error::with_os(code, i64::from(e.raw_os_error()))
}

fn io_err(e: std::io::Error) -> Error {
    match e.raw_os_error() {
        Some(n) => map_errno(Errno::from_raw_os_error(n)),
        None => Error::new(ErrorCode::Io),
    }
}

/// Existing ancestors on POSIX may legally contain any byte but `/` and NUL.
/// We still refuse `.`/`..`/empty (would not be a single downward step).
pub(crate) fn validate_existing_component(c: &str) -> Result<()> {
    if c.is_empty() || c == "." || c == ".." || c.contains('\0') || c.len() > 255 {
        return Err(Error::new(ErrorCode::InvalidName));
    }
    Ok(())
}

/// Parse an absolute POSIX path into components and open `/`.
pub(crate) fn open_root(path: &str) -> Result<(File, String, Vec<String>)> {
    // Absolute, not `//` (implementation-defined), no empty / `.` / `..`
    // components, no trailing slash (ambiguous: "the dir" vs "its contents").
    let rest = path
        .strip_prefix('/')
        .ok_or(Error::new(ErrorCode::InvalidRoot))?;
    if rest.starts_with('/') || path.contains('\0') {
        return Err(Error::new(ErrorCode::InvalidRoot));
    }
    let comps: Vec<String> = if rest.is_empty() {
        Vec::new()
    } else {
        if rest.ends_with('/') {
            return Err(Error::new(ErrorCode::InvalidRoot));
        }
        rest.split('/').map(str::to_owned).collect()
    };
    for c in &comps {
        validate_existing_component(c).map_err(|_| Error::new(ErrorCode::InvalidRoot))?;
    }
    let fd = openat(
        rustix::fs::CWD,
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .map_err(map_errno)?;
    Ok((File::from(fd), "/".to_owned(), comps))
}

fn dir_flags() -> OFlags {
    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NOCTTY
}

/// Refine ENOTDIR/ELOOP into "it is a link" when the entry is a symlink.
fn refine_link_error(parent: &File, name: &str, e: Error) -> Error {
    if matches!(
        e.code(),
        ErrorCode::NotADirectory | ErrorCode::LinkOrReparse
    ) {
        if let Ok(Some(FileKind::Symlink)) = probe_at(parent, name) {
            return Error::new(ErrorCode::LinkOrReparse);
        }
    }
    e
}

pub(crate) fn open_dir_at(parent: &File, name: &str) -> Result<File> {
    let fd = openat(parent.as_fd(), name, dir_flags(), Mode::empty())
        .map_err(map_errno)
        .map_err(|e| refine_link_error(parent, name, e))?;
    let f = File::from(fd);
    // O_DIRECTORY already guarantees a directory; re-check through the handle.
    if metadata(&f)?.kind != FileKind::Directory {
        return Err(Error::new(ErrorCode::NotADirectory));
    }
    Ok(f)
}

pub(crate) fn create_dir_at(parent: &File, name: &str) -> Result<(File, DirCreation)> {
    rustix::fs::mkdirat(parent.as_fd(), name, Mode::RWXU).map_err(map_errno)?;
    crate::hooks::fire(crate::hooks::Point::AfterMkdirBeforeOpen);
    // POSIX mkdir returns no handle: reopen by name without following links and
    // check that it is an empty directory owned by us. An *empty* directory swapped
    // in by the same user between these two calls is indistinguishable.
    let f = open_dir_at(parent, name).map_err(|e| match e.code() {
        ErrorCode::LinkOrReparse | ErrorCode::NotADirectory | ErrorCode::NotFound => {
            Error::new(ErrorCode::ClaimLost)
        }
        _ => e,
    })?;
    let m = metadata(&f)?;
    if m.owned_by_self != Some(true) || !is_empty_dir(&f)? {
        return Err(Error::new(ErrorCode::ClaimLost));
    }
    Ok((f, DirCreation::ReopenedByName))
}

pub(crate) fn open_file_read_at(parent: &File, name: &str) -> Result<File> {
    // O_NONBLOCK so that opening a FIFO never blocks; O_NOCTTY for devices.
    let fd = openat(
        parent.as_fd(),
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC | OFlags::NOCTTY,
        Mode::empty(),
    )
    .map_err(map_errno)
    .map_err(|e| refine_link_error(parent, name, e))?;
    let f = File::from(fd);
    if metadata(&f)?.kind != FileKind::File {
        return Err(Error::new(ErrorCode::NotARegularFile));
    }
    // Regular files ignore O_NONBLOCK, but clear it for clarity.
    let fl = rustix::fs::fcntl_getfl(&f).map_err(map_errno)?;
    rustix::fs::fcntl_setfl(&f, fl - OFlags::NONBLOCK).map_err(map_errno)?;
    Ok(f)
}

pub(crate) fn create_file_at(parent: &File, name: &str) -> Result<File> {
    // O_EXCL|O_CREAT fails on any existing name, including a dangling symlink.
    let fd: OwnedFd = openat(
        parent.as_fd(),
        name,
        OFlags::WRONLY
            | OFlags::CREATE
            | OFlags::EXCL
            | OFlags::NOFOLLOW
            | OFlags::CLOEXEC
            | OFlags::NOCTTY,
        Mode::RUSR | Mode::WUSR,
    )
    .map_err(map_errno)?;
    Ok(File::from(fd))
}

pub(crate) fn probe_at(parent: &File, name: &str) -> Result<Option<FileKind>> {
    match rustix::fs::statat(parent.as_fd(), name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(st) => Ok(Some(kind_of(FileType::from_raw_mode(st.st_mode as _)))),
        Err(Errno::NOENT) => Ok(None),
        Err(e) => Err(map_errno(e)),
    }
}

fn kind_of(t: FileType) -> FileKind {
    match t {
        FileType::RegularFile => FileKind::File,
        FileType::Directory => FileKind::Directory,
        FileType::Symlink => FileKind::Symlink,
        _ => FileKind::Other,
    }
}

#[allow(clippy::unnecessary_cast)] // field widths differ between libc/linux_raw backends
pub(crate) fn metadata(f: &File) -> Result<Metadata> {
    let st = rustix::fs::fstat(f).map_err(map_errno)?;
    let ns = |s: i64, n: i64| i128::from(s) * 1_000_000_000 + i128::from(n);
    Ok(Metadata {
        kind: kind_of(FileType::from_raw_mode(st.st_mode as _)),
        identity: Identity {
            volume: st.st_dev as u64,
            file: u128::from(st.st_ino as u64),
        },
        mount_id: mount_id(f),
        link_count: st.st_nlink as u64,
        size: u64::try_from(st.st_size).unwrap_or(0),
        modified_ns: ns(st.st_mtime as i64, st.st_mtime_nsec as i64),
        changed_ns: ns(st.st_ctime as i64, st.st_ctime_nsec as i64),
        owned_by_self: Some(st.st_uid as u32 == rustix::process::geteuid().as_raw()),
        readonly: (st.st_mode as u32) & 0o200 == 0,
    })
}

#[cfg(target_os = "linux")]
fn mount_id(f: &File) -> Option<u64> {
    use rustix::fs::{StatxFlags, statx};
    let sx = statx(f, "", AtFlags::EMPTY_PATH, StatxFlags::MNT_ID).ok()?;
    if StatxFlags::from_bits_retain(sx.stx_mask).contains(StatxFlags::MNT_ID) {
        Some(sx.stx_mnt_id)
    } else {
        None
    }
}

#[cfg(not(target_os = "linux"))]
fn mount_id(_f: &File) -> Option<u64> {
    None
}

fn mount_id_supported() -> bool {
    open_root("/")
        .ok()
        .is_some_and(|(root, _, _)| mount_id(&root).is_some())
}

pub(crate) fn is_empty_dir(f: &File) -> Result<bool> {
    // `Dir::read_from` reopens "." relative to the fd (same directory object).
    let mut dir = rustix::fs::Dir::read_from(f).map_err(map_errno)?;
    for entry in dir.by_ref() {
        let entry = entry.map_err(map_errno)?;
        let n = entry.file_name().to_bytes();
        if n != b"." && n != b".." {
            return Ok(false);
        }
    }
    Ok(true)
}

/// File system family and whether the volume is remote.
pub(crate) fn fs_kind(f: &File) -> Result<(FsKind, bool)> {
    fs_kind_impl(f)
}

/// Allow-list of local file systems (fail closed: anything unrecognised is refused,
/// including cluster/network file systems this crate does not know about).
pub(crate) fn fs_supported(k: FsKind) -> bool {
    if cfg!(target_os = "linux") {
        matches!(
            k,
            FsKind::Ext4
                | FsKind::Xfs
                | FsKind::Btrfs
                | FsKind::Tmpfs
                | FsKind::Zfs
                | FsKind::Overlay
        )
    } else if cfg!(target_os = "macos") {
        matches!(k, FsKind::Apfs | FsKind::Hfs)
    } else {
        false
    }
}

#[cfg(target_os = "linux")]
#[allow(clippy::unnecessary_cast)]
fn fs_kind_impl(f: &File) -> Result<(FsKind, bool)> {
    let st = rustix::fs::fstatfs(f).map_err(map_errno)?;
    // Magic numbers from linux/magic.h (f_type is signed on some ABIs).
    let k = match st.f_type as i64 as u32 {
        0xEF53 => FsKind::Ext4,
        0x5846_5342 => FsKind::Xfs,
        0x9123_683E => FsKind::Btrfs,
        0x0102_1994 => FsKind::Tmpfs,
        0x794C_7630 => FsKind::Overlay,
        0x2FC1_2FC1 => FsKind::Zfs,
        0x4d44 => FsKind::Fat,
        0x2011_BAB0 => FsKind::Fat,  // exFAT
        0x5346_544E => FsKind::Ntfs, // ntfs3 / ntfs-3g reports fuse
        0x6969 | 0x517B | 0xFF53_4D42 | 0xFE53_4D42 | 0x0102_1997 | 0x6B41_4653 => {
            FsKind::Network // nfs, smb, cifs, smb2, 9p, afs
        }
        0x6573_5546 => FsKind::Fuse,
        _ => FsKind::Other,
    };
    let remote = matches!(k, FsKind::Network | FsKind::Fuse);
    Ok((k, remote))
}

#[cfg(target_os = "macos")]
fn fs_kind_impl(f: &File) -> Result<(FsKind, bool)> {
    let st = rustix::fs::fstatfs(f).map_err(map_errno)?;
    let name: Vec<u8> = st
        .f_fstypename
        .iter()
        .take_while(|&&c| c != 0)
        .map(|&c| c as u8)
        .collect();
    let k = match name.as_slice() {
        b"apfs" => FsKind::Apfs,
        b"hfs" => FsKind::Hfs,
        b"msdos" | b"exfat" => FsKind::Fat,
        b"smbfs" | b"nfs" | b"afpfs" | b"webdav" => FsKind::Network,
        b"macfuse" | b"osxfuse" => FsKind::Fuse,
        _ => FsKind::Other,
    };
    // MNT_LOCAL = 0x1000 in <sys/mount.h>.
    let remote = (st.f_flags & 0x1000) == 0 || matches!(k, FsKind::Network | FsKind::Fuse);
    Ok((k, remote))
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn fs_kind_impl(_f: &File) -> Result<(FsKind, bool)> {
    Ok((FsKind::Other, true))
}

pub(crate) fn sync_dir(f: &File) -> Result<()> {
    rustix::fs::fsync(f).map_err(map_errno)
}

pub(crate) fn sync_file(f: &File) -> Result<()> {
    f.sync_all().map_err(io_err)
}

pub(crate) fn map_io(e: std::io::Error) -> Error {
    io_err(e)
}
