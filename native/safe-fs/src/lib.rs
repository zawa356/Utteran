//! E-C2a (Issue #86) proof of concept: a small, OS-neutral layer for
//! handle-relative, no-follow, exclusive-create file operations.
//!
//! Scope is intentionally narrow. The crate can:
//!
//! * walk an existing absolute local directory path from the volume root, one
//!   validated component at a time without following links, and **hold every
//!   ancestor** ([`DirChain`]);
//! * open existing child directories / regular files relative to a held handle;
//! * read a regular file through the same handle with a size limit and change
//!   detection ([`Dir::read_file`]);
//! * create **new** directories and files exclusively and write/flush them
//!   ([`Dir::create_file`], [`DirChain::push_new`]);
//! * report identity, volume, kind, link count and file system family
//!   ([`Metadata`], [`DirChain::fs_kind`]).
//!
//! It deliberately has **no** API to write into, truncate, overwrite, rename or
//! delete existing data. Errors are fixed codes without paths ([`Error`]). No raw
//! file descriptors, `HANDLE`s, OS types or Python types appear in the public API.
//!
//! Platform guarantees differ and are reported by [`capabilities`]; the ADR
//! (`docs/architecture/ui3/e-c2a-rust-safe-fs.md`) records the evidence.

// `unsafe` is denied crate-wide and allowed only in `sys/windows.rs` (FFI).
#![deny(unsafe_code)]

mod chain;
mod error;
pub mod hooks;
mod meta;
mod name;
mod sys;

pub use chain::{Dir, DirChain, NewFile, ReadOutcome};
pub use error::{Error, ErrorCode, Result};
pub use meta::{Capabilities, DirCreation, FileKind, FsKind, Guarantee, Identity, Metadata};
pub use name::Name;

/// Capabilities of this crate on the running platform.
pub fn capabilities() -> Capabilities {
    sys::capabilities()
}
