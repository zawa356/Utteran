//! Windows backend on `windows-sys` (the only module allowed to use `unsafe`).
//!
//! Design (see ADR §4):
//! * Every lookup is `NtCreateFile` with `OBJECT_ATTRIBUTES.RootDirectory` = the held
//!   parent handle and a single-component name, `FILE_OPEN_REPARSE_POINT` (never
//!   follow), and an explicit share mode.
//! * Directory handles are opened with `FILE_SHARE_READ | FILE_SHARE_WRITE` and **without
//!   `FILE_SHARE_DELETE`**: while held, no other opener can obtain `DELETE` access
//!   (required to rename or delete) and opening fails if someone already holds it.
//! * New directories are created with `FILE_CREATE | FILE_DIRECTORY_FILE`: the create
//!   call returns the handle of the directory it created (no reopen by name).
//! * Files being read are opened with `FILE_SHARE_READ` only (no concurrent writer,
//!   rename or delete while the handle is open).
//! * The root must be a drive-letter path whose handle is the root directory of a
//!   local NTFS volume (refuses UNC, `\\.\`, SUBST/redirected drives, network
//!   redirectors and non-NTFS file systems).
//!
//! Unsafe contract for this module: every FFI call receives pointers to live, correctly
//! sized and aligned local buffers; every returned `HANDLE` is wrapped into an
//! `OwnedHandle` exactly once, immediately after a success status; no handle is used
//! after its owner is dropped.
#![allow(unsafe_code)]

use std::fs::File;
use std::mem::{MaybeUninit, size_of};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};

use windows_sys::Wdk::Foundation::OBJECT_ATTRIBUTES;
use windows_sys::Wdk::Storage::FileSystem::{
    FILE_CREATE, FILE_DIRECTORY_FILE, FILE_NON_DIRECTORY_FILE, FILE_OPEN, FILE_OPEN_REPARSE_POINT,
    FILE_SEQUENTIAL_ONLY, FILE_SYNCHRONOUS_IO_NONALERT, FileFsDeviceInformation, NtCreateFile,
    NtQueryVolumeInformationFile,
};
use windows_sys::Wdk::System::SystemServices::{FILE_FS_DEVICE_INFORMATION, FILE_REMOTE_DEVICE};
use windows_sys::Win32::Foundation::{
    GetLastError, HANDLE, INVALID_HANDLE_VALUE, NTSTATUS, OBJ_CASE_INSENSITIVE, UNICODE_STRING,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_ATTRIBUTE_TAG_INFO, FILE_BASIC_INFO, FILE_DEVICE_DISK,
    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_WRITE,
    FILE_ID_BOTH_DIR_INFO, FILE_ID_INFO, FILE_INFO_BY_HANDLE_CLASS, FILE_LIST_DIRECTORY,
    FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_SHARE_DELETE, FILE_SHARE_MODE, FILE_SHARE_READ,
    FILE_SHARE_WRITE, FILE_STANDARD_INFO, FILE_TRAVERSE, FileAttributeTagInfo, FileBasicInfo,
    FileIdBothDirectoryInfo, FileIdBothDirectoryRestartInfo, FileIdInfo, FileStandardInfo,
    GetFileInformationByHandleEx, GetFinalPathNameByHandleW, GetVolumeInformationByHandleW,
    OPEN_EXISTING, SYNCHRONIZE, VOLUME_NAME_NONE,
};
use windows_sys::Win32::System::IO::IO_STATUS_BLOCK;

use crate::error::{Error, ErrorCode, Result};
use crate::meta::{Capabilities, DirCreation, FileKind, FsKind, Guarantee, Identity, Metadata};

pub(crate) fn capabilities() -> Capabilities {
    Capabilities {
        platform: "windows",
        handle_relative_lookup: true,
        no_follow: true,
        ancestors_pinned: Guarantee::Enforced,
        // FSCTL_SET_REPARSE_POINT on an empty held dir is not share-checked.
        held_kind_stable: Guarantee::DetectedOnly,
        dir_flush: Guarantee::NotProvided,
        read_exclusive: Guarantee::Enforced,
        dir_creation: DirCreation::Atomic,
        mount_identity: Guarantee::Enforced, // mount points are reparse points: refused
        remote_refused: Guarantee::Enforced,
    }
}

// ---- error mapping -------------------------------------------------------------

const STATUS_OBJECT_NAME_INVALID: NTSTATUS = 0xC000_0033_u32 as i32;
const STATUS_OBJECT_NAME_NOT_FOUND: NTSTATUS = 0xC000_0034_u32 as i32;
const STATUS_OBJECT_NAME_COLLISION: NTSTATUS = 0xC000_0035_u32 as i32;
const STATUS_OBJECT_PATH_NOT_FOUND: NTSTATUS = 0xC000_003A_u32 as i32;
const STATUS_OBJECT_PATH_SYNTAX_BAD: NTSTATUS = 0xC000_003B_u32 as i32;
const STATUS_ACCESS_DENIED: NTSTATUS = 0xC000_0022_u32 as i32;
const STATUS_SHARING_VIOLATION: NTSTATUS = 0xC000_0043_u32 as i32;
const STATUS_DELETE_PENDING: NTSTATUS = 0xC000_0056_u32 as i32;
const STATUS_FILE_IS_A_DIRECTORY: NTSTATUS = 0xC000_00BA_u32 as i32;
const STATUS_NOT_A_DIRECTORY: NTSTATUS = 0xC000_0103_u32 as i32;
const STATUS_STOPPED_ON_SYMLINK: NTSTATUS = 0x8000_002D_u32 as i32;
const STATUS_REPARSE_POINT_ENCOUNTERED: NTSTATUS = 0xC000_050B_u32 as i32;
const STATUS_NAME_TOO_LONG: NTSTATUS = 0xC000_0106_u32 as i32;

fn map_status(st: NTSTATUS) -> Error {
    let code = match st {
        STATUS_OBJECT_NAME_NOT_FOUND | STATUS_OBJECT_PATH_NOT_FOUND | STATUS_DELETE_PENDING => {
            ErrorCode::NotFound
        }
        STATUS_OBJECT_NAME_COLLISION => ErrorCode::AlreadyExists,
        STATUS_ACCESS_DENIED => ErrorCode::PermissionDenied,
        STATUS_SHARING_VIOLATION => ErrorCode::Busy,
        STATUS_NOT_A_DIRECTORY => ErrorCode::NotADirectory,
        STATUS_FILE_IS_A_DIRECTORY => ErrorCode::NotARegularFile,
        STATUS_OBJECT_NAME_INVALID | STATUS_OBJECT_PATH_SYNTAX_BAD | STATUS_NAME_TOO_LONG => {
            ErrorCode::InvalidName
        }
        STATUS_STOPPED_ON_SYMLINK | STATUS_REPARSE_POINT_ENCOUNTERED => ErrorCode::LinkOrReparse,
        _ => ErrorCode::Io,
    };
    Error::with_os(code, i64::from(st))
}

fn map_win32(err: u32) -> Error {
    // ERROR_FILE_NOT_FOUND 2, ERROR_PATH_NOT_FOUND 3, ERROR_ACCESS_DENIED 5,
    // ERROR_SHARING_VIOLATION 32, ERROR_FILE_EXISTS 80, ERROR_ALREADY_EXISTS 183,
    // ERROR_INVALID_NAME 123, ERROR_DIRECTORY 267, ERROR_DISK_FULL 112, ERROR_HANDLE_EOF 38.
    let code = match err {
        2 | 3 => ErrorCode::NotFound,
        5 => ErrorCode::PermissionDenied,
        32 | 33 => ErrorCode::Busy,
        80 | 183 => ErrorCode::AlreadyExists,
        123 | 161 | 206 => ErrorCode::InvalidName,
        267 => ErrorCode::NotADirectory,
        _ => ErrorCode::Io,
    };
    Error::with_os(code, i64::from(err))
}

fn last_error() -> Error {
    // SAFETY: GetLastError has no preconditions.
    map_win32(unsafe { GetLastError() })
}

pub(crate) fn map_io(e: std::io::Error) -> Error {
    match e.raw_os_error() {
        Some(n) => map_win32(n as u32),
        None => Error::new(ErrorCode::Io),
    }
}

// ---- names and roots -------------------------------------------------------------

/// Existing Windows components get the full portable rule set: those rules are
/// exactly the Win32 ambiguities (device names, trailing dot/space, ADS, ...).
pub(crate) fn validate_existing_component(c: &str) -> Result<()> {
    crate::name::validate_win32_rules(c)
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().collect()
}

/// Accepts `C:\a\b` or `\\?\C:\a\b` only. Returns (root handle, root path, components).
pub(crate) fn open_root(path: &str) -> Result<(File, String, Vec<String>)> {
    let bad = || Error::new(ErrorCode::InvalidRoot);
    let rest = path.strip_prefix(r"\\?\").unwrap_or(path);
    if rest.starts_with('\\') || rest.contains('/') || rest.contains('\0') {
        // UNC (`\\server`), `\\?\UNC\`, `\\.\`, rooted-without-drive, '/' separators.
        return Err(bad());
    }
    let mut chars = rest.chars();
    let letter = chars.next().ok_or_else(bad)?;
    if !letter.is_ascii_alphabetic() || chars.next() != Some(':') || chars.next() != Some('\\') {
        return Err(bad()); // also refuses drive-relative `C:foo`
    }
    let tail: &str = chars.as_str();
    let comps: Vec<String> = if tail.is_empty() {
        Vec::new()
    } else {
        if tail.ends_with('\\') {
            return Err(bad());
        }
        tail.split('\\').map(str::to_owned).collect()
    };
    for c in &comps {
        validate_existing_component(c).map_err(|_| bad())?;
    }
    // Resolve the drive letter in the *global* DOS device namespace. `\\?\C:\` would use
    // the per-logon device map, which a same-user process can override with
    // `DefineDosDevice`/`subst` (even for `C:`) to point at another volume.
    let letter = letter.to_ascii_uppercase();
    let global = format!(r"\\?\GLOBALROOT\GLOBAL??\{letter}:\");
    // Re-openable form for `DirChain::verify_reachable` (goes through this function).
    let root_path = format!(r"{letter}:\");
    let mut w = wide(&global);
    w.push(0);
    // SAFETY: `w` is NUL-terminated and outlives the call; null security attributes
    // and template handle are permitted.
    let h = unsafe {
        CreateFileW(
            w.as_ptr(),
            FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if h == INVALID_HANDLE_VALUE {
        // A letter missing from the global namespace (per-logon subst, mapped drive).
        let e = last_error();
        return Err(if e.code() == ErrorCode::NotFound {
            Error::new(ErrorCode::InvalidRoot)
        } else {
            e
        });
    }
    // SAFETY: `h` is a valid handle we exclusively own (success checked above).
    let root = File::from(unsafe { OwnedHandle::from_raw_handle(h as _) });
    ensure_volume_root(&root)?;
    ensure_local_disk(&root)?;
    // A local DOS mapping can shadow a global drive letter. The shown path must
    // name the same volume root; all data access still uses the global handle.
    let mut shown = wide(&format!(r"\\?\{letter}:\"));
    shown.push(0);
    // SAFETY: NUL-terminated input and flags as above; the returned handle is owned.
    let visible = unsafe {
        CreateFileW(
            shown.as_ptr(),
            FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if visible == INVALID_HANDLE_VALUE {
        return Err(bad());
    }
    // SAFETY: successful CreateFileW returned a handle exclusively owned here.
    let visible = File::from(unsafe { OwnedHandle::from_raw_handle(visible as _) });
    let visible_identity = metadata(&visible).map_err(|_| bad())?.identity;
    let global_identity = metadata(&root).map_err(|_| bad())?.identity;
    if visible_identity != global_identity {
        return Err(bad());
    }
    Ok((root, root_path, comps))
}

/// The handle must be the root directory of its volume: `GetFinalPathNameByHandleW`
/// with `VOLUME_NAME_NONE` yields exactly `\`. A SUBST drive or a drive letter
/// redirected to a folder yields a longer path and is refused.
fn ensure_volume_root(root: &File) -> Result<()> {
    let mut buf = [0u16; 64];
    // SAFETY: buffer pointer/length describe `buf`; handle is live.
    let n = unsafe {
        GetFinalPathNameByHandleW(
            root.as_raw_handle() as HANDLE,
            buf.as_mut_ptr(),
            buf.len() as u32,
            VOLUME_NAME_NONE,
        )
    };
    if n == 0 {
        return Err(last_error());
    }
    match buf.get(..n as usize) {
        Some([0x5C]) => Ok(()), // "\"
        _ => Err(Error::new(ErrorCode::InvalidRoot)),
    }
}

/// Refuse redirector / remote devices; require a disk device.
fn ensure_local_disk(root: &File) -> Result<()> {
    let mut info = MaybeUninit::<FILE_FS_DEVICE_INFORMATION>::zeroed();
    let mut iosb = MaybeUninit::<IO_STATUS_BLOCK>::zeroed();
    // SAFETY: out-pointers reference live, correctly sized locals.
    let st = unsafe {
        NtQueryVolumeInformationFile(
            root.as_raw_handle() as HANDLE,
            iosb.as_mut_ptr(),
            info.as_mut_ptr().cast(),
            size_of::<FILE_FS_DEVICE_INFORMATION>() as u32,
            FileFsDeviceInformation,
        )
    };
    if st < 0 {
        return Err(map_status(st));
    }
    // SAFETY: success status means the kernel filled the structure.
    let info = unsafe { info.assume_init() };
    // FILE_REMOTE_DEVICE_VSMB (0x80000): Windows Sandbox / container mapped folders.
    const FILE_REMOTE_DEVICE_VSMB: u32 = 0x0008_0000;
    if info.Characteristics & (FILE_REMOTE_DEVICE | FILE_REMOTE_DEVICE_VSMB) != 0
        || info.DeviceType != FILE_DEVICE_DISK
    {
        return Err(Error::new(ErrorCode::RemoteVolume));
    }
    Ok(())
}

// ---- NtCreateFile wrapper ------------------------------------------------------------

struct NtOpen {
    access: u32,
    share: FILE_SHARE_MODE,
    disposition: u32,
    options: u32,
}

fn nt_open_at(parent: &File, name: &str, o: &NtOpen) -> Result<File> {
    let w = wide(name);
    let bytes = u16::try_from(w.len() * 2).map_err(|_| Error::new(ErrorCode::InvalidName))?;
    let us = UNICODE_STRING {
        Length: bytes,
        MaximumLength: bytes,
        Buffer: w.as_ptr() as *mut u16,
    };
    let oa = OBJECT_ATTRIBUTES {
        Length: size_of::<OBJECT_ATTRIBUTES>() as u32,
        RootDirectory: parent.as_raw_handle() as HANDLE,
        ObjectName: &us,
        // Win32 semantics: case-insensitive lookup (the kernel default anyway).
        // OBJ_DONT_REPARSE (0x1000, Windows 10 1709+): fail with
        // STATUS_REPARSE_POINT_ENCOUNTERED instead of following any reparse point met
        // during this lookup. Defence in depth next to FILE_OPEN_REPARSE_POINT; its effect
        // on a RootDirectory that itself became a reparse point is a VM item (W-14).
        Attributes: OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE,
        // NULL: the new object inherits the parent's inheritable ACEs.
        SecurityDescriptor: std::ptr::null(),
        SecurityQualityOfService: std::ptr::null(),
    };
    let mut h: HANDLE = std::ptr::null_mut();
    let mut iosb = MaybeUninit::<IO_STATUS_BLOCK>::zeroed();
    // SAFETY: `oa`, `us` and `w` are live for the call; `h`/`iosb` are valid
    // out-pointers; no EA buffer; allocation size null.
    let st = unsafe {
        NtCreateFile(
            &mut h,
            o.access,
            &oa,
            iosb.as_mut_ptr(),
            std::ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            o.share,
            o.disposition,
            o.options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT,
            std::ptr::null(),
            0,
        )
    };
    // NT_SUCCESS: any non-negative status (including informational ones) returns
    // a handle that we must own and close.
    if st < 0 {
        return Err(map_status(st));
    }
    // SAFETY: success: `h` is a new handle owned by us alone.
    Ok(File::from(unsafe { OwnedHandle::from_raw_handle(h as _) }))
}

const OBJ_DONT_REPARSE: u32 = 0x0000_1000;

const DIR_ACCESS: u32 = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE;
/// No FILE_SHARE_DELETE: pins the directory against rename/delete while held.
const DIR_SHARE: FILE_SHARE_MODE = FILE_SHARE_READ | FILE_SHARE_WRITE;

fn check_dir_handle(parent: &File, f: &File, requested: &str) -> Result<()> {
    let tag = attribute_tag(f)?;
    if tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(Error::new(ErrorCode::LinkOrReparse));
    }
    if tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0 {
        return Err(Error::new(ErrorCode::NotADirectory));
    }
    if id_info(f)?.VolumeSerialNumber != id_info(parent)?.VolumeSerialNumber {
        return Err(Error::new(ErrorCode::CrossesVolume));
    }
    ensure_not_alias(f, requested)
}

/// Refuse 8.3 short names and other aliases: the opened object's own final name
/// must equal the requested component (exactly, or ASCII-case-insensitively).
fn ensure_not_alias(f: &File, requested: &str) -> Result<()> {
    let mut buf = vec![0u16; 1024];
    loop {
        // SAFETY: buffer pointer/length describe `buf`; handle is live.
        let n = unsafe {
            GetFinalPathNameByHandleW(
                f.as_raw_handle() as HANDLE,
                buf.as_mut_ptr(),
                buf.len() as u32,
                VOLUME_NAME_NONE,
            )
        };
        if n == 0 {
            return Err(last_error());
        }
        let n = n as usize;
        if n >= buf.len() {
            // Too small: `n` is the required size including NUL.
            if n > 32_768 {
                return Err(Error::new(ErrorCode::Io));
            }
            buf.resize(n + 1, 0);
            continue;
        }
        let full = String::from_utf16(buf.get(..n).unwrap_or(&[]))
            .map_err(|_| Error::new(ErrorCode::NameAliased))?;
        let last = full.rsplit('\\').next().unwrap_or("");
        return if last == requested || last.eq_ignore_ascii_case(requested) {
            Ok(())
        } else {
            Err(Error::new(ErrorCode::NameAliased))
        };
    }
}

pub(crate) fn open_dir_at(parent: &File, name: &str) -> Result<File> {
    let f = nt_open_at(
        parent,
        name,
        &NtOpen {
            access: DIR_ACCESS,
            share: DIR_SHARE,
            disposition: FILE_OPEN,
            options: FILE_DIRECTORY_FILE,
        },
    )?;
    check_dir_handle(parent, &f, name)?;
    Ok(f)
}

/// With `FILE_CREATE`, NTFS reports an existing entry of the other kind as
/// `STATUS_FILE_IS_A_DIRECTORY` / `STATUS_NOT_A_DIRECTORY` instead of a name
/// collision; for an exclusive create both mean "the name exists".
fn exists_on_create(e: Error) -> Error {
    match e.code() {
        ErrorCode::NotADirectory | ErrorCode::NotARegularFile => match e.os_code() {
            Some(n) => Error::with_os(ErrorCode::AlreadyExists, n),
            None => Error::new(ErrorCode::AlreadyExists),
        },
        _ => e,
    }
}

pub(crate) fn create_dir_at(parent: &File, name: &str) -> Result<(File, DirCreation)> {
    let f = nt_open_at(
        parent,
        name,
        &NtOpen {
            access: DIR_ACCESS,
            share: DIR_SHARE,
            disposition: FILE_CREATE,
            options: FILE_DIRECTORY_FILE,
        },
    )
    .map_err(exists_on_create)?;
    // Defence in depth; FILE_CREATE cannot return an existing object.
    check_dir_handle(parent, &f, name).map_err(|_| Error::new(ErrorCode::ClaimLost))?;
    Ok((f, DirCreation::Atomic))
}

pub(crate) fn open_file_read_at(parent: &File, name: &str) -> Result<File> {
    let f = nt_open_at(
        parent,
        name,
        &NtOpen {
            access: FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            // Read-only sharing: no concurrent writer, rename or delete.
            share: FILE_SHARE_READ,
            disposition: FILE_OPEN,
            options: FILE_NON_DIRECTORY_FILE | FILE_SEQUENTIAL_ONLY,
        },
    )?;
    let tag = attribute_tag(&f)?;
    if tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(Error::new(ErrorCode::LinkOrReparse));
    }
    ensure_not_alias(&f, name)?;
    Ok(f)
}

pub(crate) fn create_file_at(parent: &File, name: &str) -> Result<File> {
    nt_open_at(
        parent,
        name,
        &NtOpen {
            access: FILE_GENERIC_WRITE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            share: FILE_SHARE_READ,
            disposition: FILE_CREATE,
            options: FILE_NON_DIRECTORY_FILE,
        },
    )
    .map_err(exists_on_create)
}

pub(crate) fn probe_at(parent: &File, name: &str) -> Result<Option<FileKind>> {
    // Attribute-only access is not subject to share-mode conflicts.
    let r = nt_open_at(
        parent,
        name,
        &NtOpen {
            access: FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            share: FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            disposition: FILE_OPEN,
            options: 0,
        },
    );
    match r {
        Ok(f) => Ok(Some(kind_from_tag(&attribute_tag(&f)?))),
        Err(e) if e.code() == ErrorCode::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

// ---- metadata ----------------------------------------------------------------------

fn info_by_handle<T: Copy>(f: &File, class: FILE_INFO_BY_HANDLE_CLASS) -> Result<T> {
    let mut v = MaybeUninit::<T>::zeroed();
    // SAFETY: `v` is a live, aligned buffer of exactly size_of::<T>() bytes and `T`
    // is the structure documented for `class` at every call site.
    let ok = unsafe {
        GetFileInformationByHandleEx(
            f.as_raw_handle() as HANDLE,
            class,
            v.as_mut_ptr().cast(),
            size_of::<T>() as u32,
        )
    };
    if ok == 0 {
        return Err(last_error());
    }
    // SAFETY: success means the structure was filled (and it was zero-initialised).
    Ok(unsafe { v.assume_init() })
}

fn attribute_tag(f: &File) -> Result<FILE_ATTRIBUTE_TAG_INFO> {
    info_by_handle(f, FileAttributeTagInfo)
}

fn id_info(f: &File) -> Result<FILE_ID_INFO> {
    info_by_handle(f, FileIdInfo)
}

fn kind_from_tag(t: &FILE_ATTRIBUTE_TAG_INFO) -> FileKind {
    if t.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        FileKind::ReparsePoint
    } else if t.FileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
        FileKind::Directory
    } else {
        FileKind::File
    }
}

fn filetime_to_unix_ns(t: i64) -> i128 {
    (i128::from(t) - 116_444_736_000_000_000) * 100
}

pub(crate) fn metadata(f: &File) -> Result<Metadata> {
    let tag = attribute_tag(f)?;
    let id = id_info(f)?;
    let std_info: FILE_STANDARD_INFO = info_by_handle(f, FileStandardInfo)?;
    let basic: FILE_BASIC_INFO = info_by_handle(f, FileBasicInfo)?;
    Ok(Metadata {
        kind: kind_from_tag(&tag),
        identity: Identity {
            volume: id.VolumeSerialNumber,
            file: u128::from_le_bytes(id.FileId.Identifier),
        },
        mount_id: None,
        link_count: u64::from(std_info.NumberOfLinks),
        size: u64::try_from(std_info.EndOfFile).unwrap_or(0),
        modified_ns: filetime_to_unix_ns(basic.LastWriteTime),
        changed_ns: filetime_to_unix_ns(basic.ChangeTime),
        owned_by_self: None,
        readonly: basic.FileAttributes & FILE_ATTRIBUTE_READONLY != 0,
    })
}

pub(crate) fn fs_kind(f: &File) -> Result<(FsKind, bool)> {
    ensure_local_disk(f).map_err(|e| {
        if e.code() == ErrorCode::RemoteVolume {
            e
        } else {
            Error::new(ErrorCode::Io)
        }
    })?;
    let mut name = [0u16; 64];
    // SAFETY: only the FS-name buffer is requested; its pointer/length describe `name`.
    let ok = unsafe {
        GetVolumeInformationByHandleW(
            f.as_raw_handle() as HANDLE,
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            name.as_mut_ptr(),
            name.len() as u32,
        )
    };
    if ok == 0 {
        return Err(last_error());
    }
    let len = name.iter().position(|&c| c == 0).unwrap_or(name.len());
    let s = String::from_utf16_lossy(name.get(..len).unwrap_or(&[]));
    let k = match s.as_str() {
        "NTFS" => FsKind::Ntfs,
        "ReFS" => FsKind::Refs,
        "FAT" | "FAT32" | "exFAT" => FsKind::Fat,
        _ => FsKind::Other,
    };
    if k != FsKind::Ntfs {
        // Initial target: local NTFS only (fail closed for ReFS/FAT/other).
        return Err(Error::new(ErrorCode::UnsupportedFileSystem));
    }
    Ok((k, false))
}

pub(crate) fn fs_supported(k: FsKind) -> bool {
    k == FsKind::Ntfs
}

/// Note: the enumeration cursor belongs to the handle, so concurrent calls on the
/// same `Dir` from two threads may interleave (documented on `Dir::is_empty`).
pub(crate) fn is_empty_dir(f: &File) -> Result<bool> {
    // 64 KiB, 8-byte aligned buffer for FILE_ID_BOTH_DIR_INFO records.
    let mut buf = vec![0u64; 8192];
    let mut class = FileIdBothDirectoryRestartInfo;
    loop {
        // SAFETY: buffer pointer/length describe `buf` (bytes), alignment 8.
        let ok = unsafe {
            GetFileInformationByHandleEx(
                f.as_raw_handle() as HANDLE,
                class,
                buf.as_mut_ptr().cast(),
                (buf.len() * 8) as u32,
            )
        };
        if ok == 0 {
            // SAFETY: no preconditions.
            let err = unsafe { GetLastError() };
            return if err == 18 {
                Ok(true) // ERROR_NO_MORE_FILES
            } else {
                Err(map_win32(err))
            };
        }
        class = FileIdBothDirectoryInfo;
        let base = buf.as_ptr().cast::<u8>();
        let total = buf.len() * 8;
        let mut off = 0usize;
        loop {
            let hdr = size_of::<FILE_ID_BOTH_DIR_INFO>();
            if off + hdr > total {
                return Err(Error::new(ErrorCode::Io));
            }
            // SAFETY: `off + hdr <= total`, records are 8-byte aligned by the API and
            // `read_unaligned` tolerates any alignment anyway.
            let rec: FILE_ID_BOTH_DIR_INFO =
                unsafe { std::ptr::read_unaligned(base.add(off).cast()) };
            let name_off = std::mem::offset_of!(FILE_ID_BOTH_DIR_INFO, FileName);
            let name_units = rec.FileNameLength as usize / 2;
            if off + name_off + name_units * 2 > total {
                return Err(Error::new(ErrorCode::Io));
            }
            let mut name = vec![0u16; name_units];
            // SAFETY: bounds checked above; copying `name_units` u16 into `name`.
            unsafe {
                std::ptr::copy_nonoverlapping(
                    base.add(off + name_off),
                    name.as_mut_ptr().cast::<u8>(),
                    name_units * 2,
                );
            }
            if name != [0x2E] && name != [0x2E, 0x2E] {
                return Ok(false);
            }
            if rec.NextEntryOffset == 0 {
                break;
            }
            off += rec.NextEntryOffset as usize;
        }
    }
}

pub(crate) fn sync_dir(_f: &File) -> Result<()> {
    Ok(())
}

pub(crate) fn sync_file(f: &File) -> Result<()> {
    f.sync_all().map_err(map_io)
}
