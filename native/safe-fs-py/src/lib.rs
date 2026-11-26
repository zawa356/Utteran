//! Thin PyO3 binding over `utteran-safe-fs` (#86/#87). No logic lives here:
//! every method converts arguments, releases the GIL, calls the core and maps the
//! fixed error code to `SafeFsError(code, os_code)`. Paths and names are never
//! included in exception messages.

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use utteran_safe_fs as core;

create_exception!(
    utteran_native_fs,
    SafeFsError,
    PyException,
    "Fixed-code error: args = (code: str, os_code: int | None)."
);

fn err(e: core::Error) -> PyErr {
    SafeFsError::new_err((e.code().as_str(), e.os_code()))
}

fn name(s: &str) -> PyResult<core::Name> {
    core::Name::new(s).map_err(err)
}

/// Upper bound for `max_bytes` from Python: the read buffer is sized by the file, so
/// an unbounded limit could request an allocation that aborts the process.
const MAX_READ_LIMIT: u64 = 1 << 30;

fn closed() -> PyErr {
    SafeFsError::new_err((core::ErrorCode::Closed.as_str(), None::<i64>))
}

fn creation_str(d: core::DirCreation) -> &'static str {
    match d {
        core::DirCreation::Atomic => "atomic",
        core::DirCreation::ReopenedByName => "reopened_by_name",
    }
}

fn kind_str(k: core::FileKind) -> &'static str {
    match k {
        core::FileKind::File => "file",
        core::FileKind::Directory => "directory",
        core::FileKind::Symlink => "symlink",
        core::FileKind::ReparsePoint => "reparse_point",
        core::FileKind::Other => "other",
    }
}

fn meta_dict<'py>(py: Python<'py>, m: &core::Metadata) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("kind", kind_str(m.kind))?;
    d.set_item("volume", m.identity.volume)?;
    d.set_item("file_id", m.identity.file)?;
    d.set_item("mount_id", m.mount_id)?;
    d.set_item("link_count", m.link_count)?;
    d.set_item("size", m.size)?;
    Ok(d)
}

/// Capability report of the running platform.
#[pyfunction]
fn capabilities(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let c = core::capabilities();
    let d = PyDict::new(py);
    d.set_item("platform", c.platform)?;
    d.set_item("handle_relative_lookup", c.handle_relative_lookup)?;
    d.set_item("no_follow", c.no_follow)?;
    d.set_item("ancestors_pinned", c.ancestors_pinned.as_str())?;
    d.set_item("read_exclusive", c.read_exclusive.as_str())?;
    d.set_item("dir_creation", creation_str(c.dir_creation))?;
    d.set_item("mount_identity", c.mount_identity.as_str())?;
    d.set_item("remote_refused", c.remote_refused.as_str())?;
    d.set_item("held_kind_stable", c.held_kind_stable.as_str())?;
    d.set_item("dir_flush", c.dir_flush.as_str())?;
    Ok(d)
}

/// Held directory chain. All operations act on the leaf. Use as a context manager
/// or call `close()`; handles are also released when the object is collected.
#[pyclass(module = "utteran_native_fs")]
struct DirChain {
    inner: Option<core::DirChain>,
}

impl DirChain {
    fn get(&self) -> PyResult<&core::DirChain> {
        self.inner.as_ref().ok_or_else(closed)
    }
    fn get_mut(&mut self) -> PyResult<&mut core::DirChain> {
        self.inner.as_mut().ok_or_else(closed)
    }
}

#[pymethods]
impl DirChain {
    /// Walk and hold an existing absolute local directory path.
    #[staticmethod]
    fn open(py: Python<'_>, path: &str) -> PyResult<Self> {
        let path = path.to_owned();
        let c = py
            .detach(move || core::DirChain::open_absolute(&path))
            .map_err(err)?;
        Ok(Self { inner: Some(c) })
    }

    #[getter]
    fn depth(&self) -> PyResult<usize> {
        Ok(self.get()?.depth())
    }

    #[getter]
    fn fs_kind(&self) -> PyResult<&'static str> {
        Ok(self.get()?.fs_kind().as_str())
    }

    /// `(volume, file_id)` of each held directory, root first.
    fn identities(&self) -> PyResult<Vec<(u64, u128)>> {
        Ok(self
            .get()?
            .identities()
            .into_iter()
            .map(|i| (i.volume, i.file))
            .collect())
    }

    fn push_existing(&mut self, py: Python<'_>, child: &str) -> PyResult<()> {
        let n = name(child)?;
        let c = self.get_mut()?;
        py.detach(|| c.push_existing(&n)).map_err(err)
    }

    /// Create a new child directory exclusively and descend; returns how the handle
    /// was obtained (`"atomic"` or `"reopened_by_name"`).
    fn push_new(&mut self, py: Python<'_>, child: &str) -> PyResult<&'static str> {
        let n = name(child)?;
        let c = self.get_mut()?;
        py.detach(|| c.push_new(&n)).map(creation_str).map_err(err)
    }

    fn verify_reachable(&self, py: Python<'_>) -> PyResult<()> {
        let c = self.get()?;
        py.detach(|| c.verify_reachable()).map_err(err)
    }

    /// `(index, kind)` of the nearest held directory containing `entry`, or None.
    fn find_in_ancestors(
        &self,
        py: Python<'_>,
        entry: &str,
    ) -> PyResult<Option<(usize, &'static str)>> {
        let n = name(entry)?;
        let c = self.get()?;
        let r = py.detach(|| c.find_in_ancestors(&n)).map_err(err)?;
        Ok(r.map(|(i, k)| (i, kind_str(k))))
    }

    fn probe(&self, py: Python<'_>, entry: &str) -> PyResult<Option<&'static str>> {
        let n = name(entry)?;
        let c = self.get()?;
        let r = py.detach(|| c.leaf().probe(&n)).map_err(err)?;
        Ok(r.map(kind_str))
    }

    /// Read a regular file of the leaf through one handle (size-limited, at most
    /// 1 GiB). Change detection is heuristic on POSIX: verify a digest yourself.
    fn read_file<'py>(
        &self,
        py: Python<'py>,
        entry: &str,
        max_bytes: u64,
    ) -> PyResult<Bound<'py, PyBytes>> {
        Ok(self.read_file_with_metadata(py, entry, max_bytes)?.0)
    }

    /// Like `read_file`, also returning the handle metadata observed for the read.
    fn read_file_with_metadata<'py>(
        &self,
        py: Python<'py>,
        entry: &str,
        max_bytes: u64,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyDict>)> {
        if max_bytes > MAX_READ_LIMIT {
            return Err(SafeFsError::new_err((
                core::ErrorCode::TooLarge.as_str(),
                None::<i64>,
            )));
        }
        let n = name(entry)?;
        let c = self.get()?;
        let out = py
            .detach(|| c.leaf().read_file(&n, max_bytes))
            .map_err(err)?;
        // One copy into a Python bytes object.
        Ok((PyBytes::new(py, &out.bytes), meta_dict(py, &out.metadata)?))
    }

    /// Whether the leaf has no entries.
    fn is_empty(&self, py: Python<'_>) -> PyResult<bool> {
        let c = self.get()?;
        py.detach(|| c.leaf().is_empty()).map_err(err)
    }

    /// Flush the leaf directory's metadata (POSIX fsync; no-op on Windows, see
    /// `capabilities()["dir_flush"]`).
    fn sync(&self, py: Python<'_>) -> PyResult<()> {
        let c = self.get()?;
        py.detach(|| c.leaf().sync()).map_err(err)
    }

    /// Re-check every held handle still is the same directory (not a reparse point).
    fn verify_held(&self, py: Python<'_>) -> PyResult<()> {
        let c = self.get()?;
        py.detach(|| c.verify_held()).map_err(err)
    }

    /// Create `entry` exclusively in the leaf, write all of `data`, flush, close.
    /// On failure the partial file is left in place (nothing is deleted).
    fn write_new_file<'py>(
        &self,
        py: Python<'py>,
        entry: &str,
        data: &[u8],
    ) -> PyResult<Bound<'py, PyDict>> {
        let n = name(entry)?;
        let c = self.get()?;
        let m = py
            .detach(|| {
                let mut f = c.leaf().create_file(&n)?;
                f.write_all(data)?;
                f.finish()
            })
            .map_err(err)?;
        meta_dict(py, &m)
    }

    /// Release every held handle. Further calls raise `SafeFsError("closed")`.
    fn close(&mut self) {
        self.inner = None;
    }

    #[getter]
    fn closed(&self) -> bool {
        self.inner.is_none()
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &mut self,
        _t: Option<Bound<'_, PyAny>>,
        _v: Option<Bound<'_, PyAny>>,
        _tb: Option<Bound<'_, PyAny>>,
    ) -> bool {
        self.close();
        false
    }
}

#[pymodule]
fn utteran_native_fs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(capabilities, m)?)?;
    m.add_class::<DirChain>()?;
    m.add("SafeFsError", m.py().get_type::<SafeFsError>())?;
    Ok(())
}
