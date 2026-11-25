//! Platform-neutral metadata and capability types (no raw handles, no OS types).

/// Object kind as observed through the handle (never by following a link).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FileKind {
    /// Regular file.
    File,
    /// Directory.
    Directory,
    /// Symbolic link (POSIX) — only observable through probe operations.
    Symlink,
    /// Windows reparse point of any tag (junction, symlink, cloud placeholder, ...).
    ReparsePoint,
    /// FIFO, socket, device or anything else.
    Other,
}

/// Identity of an object: equal identities mean the same object on the same volume.
///
/// POSIX: `(st_dev, st_ino)`; Linux additionally records the mount id when the
/// kernel reports it (`statx(STATX_MNT_ID)`, 5.8+), which distinguishes bind mounts
/// of the same file system. Windows: `FILE_ID_INFO` (64-bit volume serial + 128-bit
/// file id). Values are opaque and only comparable on the same machine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Identity {
    /// Volume / device number.
    pub volume: u64,
    /// File number within the volume (128-bit on ReFS/NTFS FILE_ID_INFO).
    pub file: u128,
}

/// File system family of the volume holding an object.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum FsKind {
    /// Windows NTFS.
    Ntfs,
    /// Windows ReFS.
    Refs,
    /// FAT12/16/32 or exFAT.
    Fat,
    /// Linux ext2/3/4.
    Ext4,
    /// Linux XFS.
    Xfs,
    /// Linux Btrfs.
    Btrfs,
    /// Linux tmpfs.
    Tmpfs,
    /// Linux overlayfs (containers).
    Overlay,
    /// ZFS.
    Zfs,
    /// macOS APFS.
    Apfs,
    /// macOS HFS+.
    Hfs,
    /// Network file system (NFS, SMB/CIFS, 9p, WebDAV, ...).
    Network,
    /// FUSE (unknown backing store).
    Fuse,
    /// Anything else.
    Other,
}

impl FsKind {
    /// Stable identifier.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ntfs => "ntfs",
            Self::Refs => "refs",
            Self::Fat => "fat",
            Self::Ext4 => "ext4",
            Self::Xfs => "xfs",
            Self::Btrfs => "btrfs",
            Self::Tmpfs => "tmpfs",
            Self::Overlay => "overlay",
            Self::Zfs => "zfs",
            Self::Apfs => "apfs",
            Self::Hfs => "hfs",
            Self::Network => "network",
            Self::Fuse => "fuse",
            Self::Other => "other",
        }
    }
}

/// Metadata read from an open handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Metadata {
    /// Object kind.
    pub kind: FileKind,
    /// Object identity.
    pub identity: Identity,
    /// Mount id (Linux `statx` 5.8+ only). `None` = not reported by this OS/kernel.
    pub mount_id: Option<u64>,
    /// Hard link count.
    pub link_count: u64,
    /// Size in bytes (files).
    pub size: u64,
    /// Last modification, nanoseconds since the Unix epoch (Windows: converted).
    pub modified_ns: i128,
    /// POSIX ctime / Windows ChangeTime, nanoseconds since the Unix epoch.
    pub changed_ns: i128,
    /// POSIX: owner uid is the effective uid. Windows: `None` (ACL-based; not modelled).
    pub owned_by_self: Option<bool>,
    /// Windows read-only attribute / POSIX: no owner write bit.
    pub readonly: bool,
}

/// How strongly a property is guaranteed on this platform.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Guarantee {
    /// The OS refuses the competing operation while the guarantee holds.
    Enforced,
    /// The competing operation can happen; this crate detects it afterwards
    /// at the documented check points only.
    DetectedOnly,
    /// Not provided at all on this platform.
    NotProvided,
}

impl Guarantee {
    /// Stable identifier.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Enforced => "enforced",
            Self::DetectedOnly => "detected_only",
            Self::NotProvided => "not_provided",
        }
    }
}

/// How a new directory's handle was obtained.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DirCreation {
    /// The create call itself returned the handle (Windows `NtCreateFile(FILE_CREATE)`).
    /// The handle is the directory this call created.
    Atomic,
    /// `mkdirat` followed by `openat(O_NOFOLLOW)` (POSIX). The handle is checked to be
    /// an empty directory owned by us, but an empty directory swapped in between the
    /// two calls by the same user cannot be distinguished.
    ReopenedByName,
}

/// Capability report for the running platform. Values describe this crate's design
/// on the platform; the evidence for each is recorded in the ADR, not here.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Capabilities {
    /// `"linux" | "macos" | "windows" | "other-unix"`.
    pub platform: &'static str,
    /// Child lookups are relative to a held handle, one component at a time.
    pub handle_relative_lookup: bool,
    /// Final-component links/reparse points are never followed.
    pub no_follow: bool,
    /// Held ancestors cannot be renamed/deleted by others while held.
    pub ancestors_pinned: Guarantee,
    /// A file being read cannot be written/renamed by others during the read.
    pub read_exclusive: Guarantee,
    /// How new directory handles are obtained.
    pub dir_creation: DirCreation,
    /// Bind mounts / mount crossings are distinguishable by identity.
    pub mount_identity: Guarantee,
    /// Remote volumes are recognised and refused at the root.
    pub remote_refused: Guarantee,
}
