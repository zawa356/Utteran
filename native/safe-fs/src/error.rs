//! Fixed, value-free error codes.
//!
//! Errors never carry paths, names or file contents. The optional OS code is a
//! number (errno / Win32 error / NTSTATUS) for diagnostics; callers that log or
//! expose errors publicly should log only [`ErrorCode::as_str`].

use std::fmt;

/// Stable error codes. Adding a variant is a compatible change; renaming one is not.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum ErrorCode {
    /// A single path component failed validation (see [`crate::Name`]).
    InvalidName,
    /// The starting path is not an absolute local path of a supported form.
    InvalidRoot,
    /// UNC/SMB/NFS/remote or otherwise non-local volume.
    RemoteVolume,
    /// File system type not in the supported set for this platform.
    UnsupportedFileSystem,
    /// A component is a symlink, junction, mount point or other reparse point.
    LinkOrReparse,
    /// Expected a directory, found something else.
    NotADirectory,
    /// Expected a regular file, found something else (FIFO, device, dir, ...).
    NotARegularFile,
    /// A regular file has more than one hard link.
    HardLinked,
    /// A component crossed onto a different volume / mount.
    CrossesVolume,
    /// The name exists (exclusive create refused, or the case-folded name collides).
    AlreadyExists,
    /// The name does not exist.
    NotFound,
    /// Access denied by permissions/ACL.
    PermissionDenied,
    /// Windows sharing violation or another holder prevents the requested access.
    Busy,
    /// File is larger than the caller's limit.
    TooLarge,
    /// File changed (size/identity/time) while it was being read.
    ChangedDuringRead,
    /// A newly created directory could not be proven to be the one we created.
    ClaimLost,
    /// The name resolved to an alias (e.g. 8.3 short name) instead of itself.
    NameAliased,
    /// The held chain is no longer reachable by its names (detect-only result).
    Unreachable,
    /// The platform lacks a primitive required for the guarantee requested.
    Unsupported,
    /// Short write, flush failure or other I/O error while writing.
    WriteFailed,
    /// The object has already been closed.
    Closed,
    /// Any other OS error.
    Io,
}

impl ErrorCode {
    /// Stable snake_case identifier, safe to log and expose.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::InvalidName => "invalid_name",
            Self::InvalidRoot => "invalid_root",
            Self::RemoteVolume => "remote_volume",
            Self::UnsupportedFileSystem => "unsupported_file_system",
            Self::LinkOrReparse => "link_or_reparse",
            Self::NotADirectory => "not_a_directory",
            Self::NotARegularFile => "not_a_regular_file",
            Self::HardLinked => "hard_linked",
            Self::CrossesVolume => "crosses_volume",
            Self::AlreadyExists => "already_exists",
            Self::NotFound => "not_found",
            Self::PermissionDenied => "permission_denied",
            Self::Busy => "busy",
            Self::TooLarge => "too_large",
            Self::ChangedDuringRead => "changed_during_read",
            Self::ClaimLost => "claim_lost",
            Self::NameAliased => "name_aliased",
            Self::Unreachable => "unreachable",
            Self::Unsupported => "unsupported",
            Self::WriteFailed => "write_failed",
            Self::Closed => "closed",
            Self::Io => "io",
        }
    }
}

/// Error value: a fixed code plus an optional numeric OS code. No strings from input.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Error {
    code: ErrorCode,
    os_code: Option<i64>,
}

impl Error {
    pub(crate) const fn new(code: ErrorCode) -> Self {
        Self {
            code,
            os_code: None,
        }
    }

    pub(crate) const fn with_os(code: ErrorCode, os: i64) -> Self {
        Self {
            code,
            os_code: Some(os),
        }
    }

    /// The fixed error code.
    pub fn code(&self) -> ErrorCode {
        self.code
    }

    /// Raw OS error number (errno, Win32 error or NTSTATUS), if any.
    pub fn os_code(&self) -> Option<i64> {
        self.os_code
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code.as_str())
    }
}

impl std::error::Error for Error {}

impl From<ErrorCode> for Error {
    fn from(code: ErrorCode) -> Self {
        Self::new(code)
    }
}

/// Result alias.
pub type Result<T> = std::result::Result<T, Error>;
