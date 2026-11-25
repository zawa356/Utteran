//! OS backends. Exactly one is compiled; there is no generic path-based fallback.

#[cfg(unix)]
mod unix;
#[cfg(unix)]
pub(crate) use unix::*;

#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::*;

#[cfg(not(any(unix, windows)))]
compile_error!("utteran-safe-fs supports only unix and windows targets");
