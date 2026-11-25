//! Deterministic race points for this crate's tests (`test-hooks` feature only).
//!
//! Without the feature every function here is an empty inline no-op, so release
//! builds and the Python binding carry no hook code.

/// Points where a test can run code synchronously inside an operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
pub enum Point {
    /// POSIX: after `mkdirat` succeeded, before the new directory is reopened.
    AfterMkdirBeforeOpen,
    /// After a file for reading was opened and checked, before the first read.
    AfterReadOpen,
    /// Before each write chunk of [`crate::NewFile::write_all`]; argument is the
    /// number of bytes already written.
    BeforeWriteChunk(u64),
}

#[cfg(feature = "test-hooks")]
mod imp {
    use super::Point;
    use std::cell::RefCell;

    type Hook = Box<dyn FnMut(Point) -> bool>;
    // Thread-local so that tests running in parallel never see each other's hooks.
    thread_local! {
        static HOOK: RefCell<Option<Hook>> = const { RefCell::new(None) };
    }

    /// Install a hook for operations on the current thread (replaces any previous
    /// one). Returning `false` at `BeforeWriteChunk` injects a write failure.
    pub fn set(hook: impl FnMut(Point) -> bool + 'static) {
        HOOK.with(|h| *h.borrow_mut() = Some(Box::new(hook)));
    }

    /// Remove the current thread's hook.
    pub fn clear() {
        HOOK.with(|h| *h.borrow_mut() = None);
    }

    pub(crate) fn fire(p: Point) -> bool {
        // Take the hook out while it runs so it may itself call into the crate.
        let taken = HOOK.with(|h| h.borrow_mut().take());
        match taken {
            Some(mut f) => {
                let r = f(p);
                HOOK.with(|h| {
                    let mut slot = h.borrow_mut();
                    if slot.is_none() {
                        *slot = Some(f);
                    }
                });
                r
            }
            None => true,
        }
    }
}

#[cfg(feature = "test-hooks")]
pub use imp::{clear, set};

#[cfg(feature = "test-hooks")]
pub(crate) use imp::fire;

#[cfg(not(feature = "test-hooks"))]
#[inline(always)]
pub(crate) fn fire(_p: Point) -> bool {
    true
}
