//! Public handle types. Internally each wraps a `std::fs::File` owning an fd/HANDLE;
//! the raw handle is never exposed.

use std::fs::File;
use std::io::{Read, Write};

use crate::error::{Error, ErrorCode, Result};
use crate::hooks::{self, Point};
use crate::meta::{DirCreation, FileKind, FsKind, Identity, Metadata};
use crate::name::Name;
use crate::sys;

/// Write chunk size for [`NewFile::write_all`] (also the hook granularity).
const WRITE_CHUNK: usize = 1 << 20;

/// An open directory handle. Dropping it closes the handle.
pub struct Dir {
    file: File,
}

impl std::fmt::Debug for Dir {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Dir(<handle>)")
    }
}

/// Children reached from a held directory must stay on its volume and, on Linux, its
/// mount (bind mounts share `st_dev`; only the mount id tells them apart). On Linux a
/// missing mount id on either side cannot be verified and is refused (fail closed).
fn same_mount(parent: &Metadata, child: &Metadata) -> Result<()> {
    if parent.identity.volume != child.identity.volume {
        return Err(Error::new(ErrorCode::CrossesVolume));
    }
    match (parent.mount_id, child.mount_id) {
        (Some(a), Some(b)) if a != b => Err(Error::new(ErrorCode::CrossesVolume)),
        (Some(_), Some(_)) => Ok(()),
        _ if cfg!(target_os = "linux") => Err(Error::new(ErrorCode::CrossesVolume)),
        _ => Ok(()),
    }
}

/// Bytes read from a file plus the metadata observed before and after the read.
/// `Debug` prints only the length (contents may be private).
#[derive(Clone, PartialEq, Eq)]
pub struct ReadOutcome {
    /// Exactly the bytes that were read through the handle.
    pub bytes: Vec<u8>,
    /// Metadata of the handle (equal before and after the read).
    pub metadata: Metadata,
}

impl std::fmt::Debug for ReadOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ReadOutcome")
            .field("len", &self.bytes.len())
            .field("metadata", &self.metadata)
            .finish()
    }
}

impl Dir {
    fn new(file: File) -> Self {
        Self { file }
    }

    /// Metadata of this directory, read from the handle.
    pub fn metadata(&self) -> Result<Metadata> {
        sys::metadata(&self.file)
    }

    /// Whether the directory has no entries other than `.`/`..`. On Windows the
    /// enumeration cursor belongs to the handle: do not call concurrently on one `Dir`.
    pub fn is_empty(&self) -> Result<bool> {
        sys::is_empty_dir(&self.file)
    }

    /// Open an existing child directory (no-follow, directories only).
    pub fn open_dir(&self, name: &Name) -> Result<Dir> {
        let child = sys::open_dir_at(&self.file, name.as_str()).map(Dir::new)?;
        same_mount(&self.metadata()?, &child.metadata()?)?;
        Ok(child)
    }

    /// Create a new child directory exclusively and return its handle.
    ///
    /// Fails with [`ErrorCode::AlreadyExists`] for any existing entry of that name
    /// (including links and case-insensitive collisions). See [`DirCreation`] for
    /// how strongly the returned handle is tied to the created directory.
    pub fn create_dir(&self, name: &Name) -> Result<(Dir, DirCreation)> {
        let (f, how) = sys::create_dir_at(&self.file, name.as_str())?;
        Ok((Dir::new(f), how))
    }

    /// Kind of the entry `name` without following links; `None` if absent.
    ///
    /// This is an observation at one instant (for policies such as "refuse if an
    /// ancestor contains `.git`"); it is not a guarantee that the entry stays.
    pub fn probe(&self, name: &Name) -> Result<Option<FileKind>> {
        sys::probe_at(&self.file, name.as_str())
    }

    /// Read a regular file through one handle.
    ///
    /// Refuses links/reparse points, non-regular files, hard-linked files and files
    /// larger than `max_bytes`. Reads at most `max_bytes + 1` bytes and requires the
    /// handle's metadata (identity, size, mtime, ctime) to be equal before and after
    /// the read and the byte count to equal the size; otherwise
    /// [`ErrorCode::ChangedDuringRead`]. On Windows the file is opened with
    /// `FILE_SHARE_READ` only, so concurrent writers/renamers are refused by the OS.
    ///
    /// On POSIX the change detection is a heuristic: a same-size in-place overwrite
    /// within one timestamp tick is not detected. Callers that need integrity must
    /// verify the returned bytes against an expected digest (E-C1 checks SHA-256).
    /// POSIX opens the entry before checking its type (`O_NONBLOCK|O_NOCTTY` limit the
    /// side effects of opening a device node).
    pub fn read_file(&self, name: &Name, max_bytes: u64) -> Result<ReadOutcome> {
        let mut f = sys::open_file_read_at(&self.file, name.as_str())?;
        let before = sys::metadata(&f)?;
        // A file bind-mounted over the name is on another mount.
        same_mount(&self.metadata()?, &before)?;
        if before.kind != FileKind::File {
            return Err(Error::new(ErrorCode::NotARegularFile));
        }
        if before.link_count != 1 {
            return Err(Error::new(ErrorCode::HardLinked));
        }
        if before.size > max_bytes {
            return Err(Error::new(ErrorCode::TooLarge));
        }
        hooks::fire(Point::AfterReadOpen);
        let cap = usize::try_from(before.size).map_err(|_| Error::new(ErrorCode::TooLarge))?;
        let mut bytes = Vec::with_capacity(cap);
        (&mut f)
            .take(max_bytes.saturating_add(1))
            .read_to_end(&mut bytes)
            .map_err(sys::map_io)?;
        if bytes.len() as u64 > max_bytes {
            return Err(Error::new(ErrorCode::ChangedDuringRead));
        }
        let after = sys::metadata(&f)?;
        if after != before || bytes.len() as u64 != before.size {
            return Err(Error::new(ErrorCode::ChangedDuringRead));
        }
        Ok(ReadOutcome {
            bytes,
            metadata: after,
        })
    }

    /// Create a new regular file exclusively (`O_CREAT|O_EXCL|O_NOFOLLOW` /
    /// `NtCreateFile(FILE_CREATE, FILE_OPEN_REPARSE_POINT)`).
    pub fn create_file(&self, name: &Name) -> Result<NewFile> {
        let file = sys::create_file_at(&self.file, name.as_str())?;
        Ok(NewFile {
            file,
            written: 0,
            failed: false,
        })
    }

    /// Flush directory metadata (POSIX `fsync(dirfd)`). On Windows this is a no-op
    /// that returns `Ok` (directory handles are not opened for write; NTFS journals
    /// metadata) — see the ADR for the durability gap.
    pub fn sync(&self) -> Result<()> {
        sys::sync_dir(&self.file)
    }
}

/// A file created by this crate. Only appending writes from offset 0 are possible.
///
/// Dropping without [`NewFile::finish`] closes the handle and leaves whatever was
/// written (nothing is deleted).
pub struct NewFile {
    file: File,
    written: u64,
    failed: bool,
}

impl std::fmt::Debug for NewFile {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NewFile")
            .field("written", &self.written)
            .finish()
    }
}

impl NewFile {
    /// Write all of `data` (in 1 MiB chunks). Any error is sticky: later calls fail.
    pub fn write_all(&mut self, data: &[u8]) -> Result<()> {
        if self.failed {
            return Err(Error::new(ErrorCode::WriteFailed));
        }
        for chunk in data.chunks(WRITE_CHUNK) {
            if !hooks::fire(Point::BeforeWriteChunk(self.written)) {
                self.failed = true;
                return Err(Error::new(ErrorCode::WriteFailed));
            }
            if let Err(e) = self.file.write_all(chunk) {
                self.failed = true;
                let os = sys::map_io(e);
                return Err(match os.os_code() {
                    Some(n) => Error::with_os(ErrorCode::WriteFailed, n),
                    None => Error::new(ErrorCode::WriteFailed),
                });
            }
            self.written += chunk.len() as u64;
        }
        Ok(())
    }

    /// Bytes written so far.
    pub fn written(&self) -> u64 {
        self.written
    }

    /// Flush data and metadata to stable storage (`fsync` / `F_FULLFSYNC` /
    /// `FlushFileBuffers`), then return the file's metadata and close it.
    pub fn finish(self) -> Result<Metadata> {
        if self.failed {
            return Err(Error::new(ErrorCode::WriteFailed));
        }
        sys::sync_file(&self.file).map_err(|e| match e.os_code() {
            Some(n) => Error::with_os(ErrorCode::WriteFailed, n),
            None => Error::new(ErrorCode::WriteFailed),
        })?;
        let m = sys::metadata(&self.file)?;
        if m.size != self.written {
            return Err(Error::new(ErrorCode::WriteFailed));
        }
        Ok(m)
    }
}

struct Held {
    dir: Dir,
    /// Component name used to reach this entry from its parent (`None` for the root).
    name: Option<String>,
    meta: Metadata,
}

/// A chain of held directory handles from the volume root to a leaf.
///
/// Every element stays open for the life of the chain. On Windows the handles are
/// opened without `FILE_SHARE_DELETE`, so no other process can rename or delete any
/// of them while held (and opening fails if another process already holds DELETE
/// access). On POSIX renames cannot be prevented; [`DirChain::verify_reachable`]
/// detects them at the moment it is called.
pub struct DirChain {
    held: Vec<Held>,
    /// Root in the form `sys::open_root` accepts (`/`, `\\?\C:\`); never logged.
    root_path: String,
    fs: FsKind,
}

impl std::fmt::Debug for DirChain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DirChain")
            .field("depth", &self.held.len())
            .field("fs", &self.fs)
            .finish()
    }
}

impl DirChain {
    /// Walk an existing absolute local directory path from its volume root.
    ///
    /// POSIX: `/a/b` (no `//`, `.`, `..`, empty or trailing components).
    /// Windows: `C:\a\b` or `\\?\C:\a\b` on a local NTFS volume; UNC, device
    /// namespaces, SUBST/redirected drives, network and non-NTFS volumes are refused.
    /// Every component is opened relative to its parent without following links;
    /// links, junctions and any reparse point are refused.
    pub fn open_absolute(path: &str) -> Result<Self> {
        let (root, root_path, comps) = sys::open_root(path)?;
        let root_meta = sys::metadata(&root)?;
        let mut held = vec![Held {
            dir: Dir::new(root),
            name: None,
            meta: root_meta,
        }];
        for c in comps {
            sys::validate_existing_component(&c)?;
            let parent = held.last().ok_or(Error::new(ErrorCode::Io))?;
            let f = sys::open_dir_at(&parent.dir.file, &c)?;
            let meta = sys::metadata(&f)?;
            held.push(Held {
                dir: Dir::new(f),
                name: Some(c),
                meta,
            });
        }
        // The leaf decides where data is written: its file system must be local and
        // on the allow-list (fail closed for anything unrecognised).
        let leaf = held.last().ok_or(Error::new(ErrorCode::Io))?;
        let (fs, remote) = sys::fs_kind(&leaf.dir.file)?;
        if remote {
            return Err(Error::new(ErrorCode::RemoteVolume));
        }
        if !sys::fs_supported(fs) {
            return Err(Error::new(ErrorCode::UnsupportedFileSystem));
        }
        Ok(Self {
            held,
            root_path,
            fs,
        })
    }

    /// The deepest held directory.
    pub fn leaf(&self) -> &Dir {
        // `held` always contains at least the root.
        match self.held.last() {
            Some(h) => &h.dir,
            None => unreachable_root(),
        }
    }

    /// Number of held directories including the root.
    pub fn depth(&self) -> usize {
        self.held.len()
    }

    /// Identities of all held directories, root first.
    pub fn identities(&self) -> Vec<Identity> {
        self.held.iter().map(|h| h.meta.identity).collect()
    }

    /// File system family of the leaf's volume.
    pub fn fs_kind(&self) -> FsKind {
        self.fs
    }

    fn leaf_meta(&self) -> Option<&Metadata> {
        self.held.last().map(|h| &h.meta)
    }

    fn same_mount(&self, m: &Metadata) -> Result<()> {
        match self.leaf_meta() {
            Some(leaf) => same_mount(leaf, m),
            None => Err(Error::new(ErrorCode::Io)),
        }
    }

    /// Descend into an existing child directory of the leaf. Unlike the initial
    /// walk, the child must be on the same volume (and, where reported, the same
    /// mount) as the leaf.
    pub fn push_existing(&mut self, name: &Name) -> Result<()> {
        let dir = self.leaf().open_dir(name)?;
        let meta = dir.metadata()?;
        self.same_mount(&meta)?;
        self.held.push(Held {
            dir,
            name: Some(name.as_str().to_owned()),
            meta,
        });
        Ok(())
    }

    /// Create a new child directory of the leaf exclusively and descend into it.
    pub fn push_new(&mut self, name: &Name) -> Result<DirCreation> {
        let (dir, how) = self.leaf().create_dir(name)?;
        let meta = dir.metadata()?;
        self.same_mount(&meta)?;
        self.held.push(Held {
            dir,
            name: Some(name.as_str().to_owned()),
            meta,
        });
        Ok(how)
    }

    /// Re-walk the chain's names from a freshly opened root and require every
    /// element to have the held identity. Detect-only: the answer is valid for the
    /// instant of the check (POSIX), or for as long as the chain is held (Windows,
    /// where held elements cannot be renamed).
    pub fn verify_reachable(&self) -> Result<()> {
        let names: Vec<&str> = self.held.iter().filter_map(|h| h.name.as_deref()).collect();
        let (root, _, _) = sys::open_root(&self.root_path)?;
        let mut cur = root;
        let mut metas = vec![sys::metadata(&cur)?];
        for n in &names {
            let next = sys::open_dir_at(&cur, n).map_err(|_| Error::new(ErrorCode::Unreachable))?;
            metas.push(sys::metadata(&next)?);
            cur = next;
        }
        let ok = metas.len() == self.held.len()
            && metas
                .iter()
                .zip(&self.held)
                .all(|(m, h)| m.identity == h.meta.identity && m.mount_id == h.meta.mount_id);
        if ok {
            Ok(())
        } else {
            Err(Error::new(ErrorCode::Unreachable))
        }
    }

    /// Re-read every held handle and require it to still be a directory (not a
    /// reparse point) with the identity recorded when it was opened. Through the
    /// handles only; names are not consulted. Call after writing when
    /// [`crate::Capabilities::held_kind_stable`] is `DetectedOnly` (Windows).
    pub fn verify_held(&self) -> Result<()> {
        for h in &self.held {
            let m = h.dir.metadata()?;
            if m.kind != FileKind::Directory {
                return Err(Error::new(ErrorCode::LinkOrReparse));
            }
            if m.identity != h.meta.identity {
                return Err(Error::new(ErrorCode::ClaimLost));
            }
        }
        Ok(())
    }

    /// Find the nearest held directory (leaf first) that contains an entry `name`
    /// of any kind (links included, not followed). Returns its index (0 = root) and
    /// the entry kind. Policy-free primitive for checks such as "refuse output under
    /// any ancestor containing `.git`".
    pub fn find_in_ancestors(&self, name: &Name) -> Result<Option<(usize, FileKind)>> {
        for (i, h) in self.held.iter().enumerate().rev() {
            if let Some(k) = h.dir.probe(name)? {
                return Ok(Some((i, k)));
            }
        }
        Ok(None)
    }
}

#[cold]
fn unreachable_root() -> ! {
    // `DirChain` is only constructed with the root element and never pops.
    std::process::abort()
}
