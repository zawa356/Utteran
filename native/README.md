# native/ — E-C2a safe-fs PoC (Issue #86)

Product-unconnected proof of concept. Nothing in the product build (`pyproject.toml`,
`uv.lock`, PyInstaller, installer) or the product CI gate depends on this directory.

- `safe-fs/` — `utteran-safe-fs`, the OS-neutral core (no Python/UI dependency).
- `safe-fs-py/` — `utteran-safe-fs-py`, a thin PyO3 binding built as one abi3 wheel (CPython 3.11+).
- Decision record and evidence: `docs/architecture/ui3/e-c2a-rust-safe-fs.md`.
- Windows VM hand-off: `docs/architecture/ui3/e-c2a-windows-vm-handoff.md`.

```bash
cd native
cargo test -p utteran-safe-fs
cargo deny check
uvx --from maturin==1.16.0 maturin build --release --locked -m safe-fs-py/Cargo.toml -o dist
```
