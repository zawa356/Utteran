"""Read-only NVIDIA identity/memory queries through the driver's NVML library (ctypes).

Used only for B4 CUDA evidence (``fingerprints`` and the explicit smoke child). NVML ships
with the display driver (``nvml.dll`` / ``libnvidia-ml.so.1``); nothing is installed, no
process is spawned and no CUDA context is created here. Any failure returns ``None``
(unknown) - never a guessed value.

Ordinal mapping: NVML's own index order is not guaranteed to match CUDA's ordinal (and
NVML ignores ``CUDA_VISIBLE_DEVICES``). Devices are therefore identified by UUID; the
CUDA ordinal -> UUID link is taken from the CUDA driver API (``cuDeviceGetUuid``) in the
process that actually ran on that ordinal (``cuda_ordinal_uuid``), never inferred.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Any

NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF
_SUCCESS = 0
_BUFFER = 96


@dataclass(frozen=True)
class NvmlGpu:
    uuid: str
    name: str
    memory_total: int | None


@dataclass(frozen=True)
class NvmlSnapshot:
    driver_version: str
    cuda_driver_version: int | None
    gpus: tuple[NvmlGpu, ...]  # sorted by UUID (enumeration order is not stable)


class _Memory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _ProcessV3(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("usedGpuMemory", ctypes.c_ulonglong),
        ("gpuInstanceId", ctypes.c_uint),
        ("computeInstanceId", ctypes.c_uint),
    ]


def _load(name_windows: str, name_posix: str) -> Any | None:
    try:
        if sys.platform == "win32":
            loader = getattr(ctypes, "WinDLL")  # noqa: B009 - typeshed guards it by platform
            return loader(name_windows)
        return ctypes.CDLL(name_posix)
    except OSError:
        return None


def load_nvml() -> Any | None:
    return _load("nvml.dll", "libnvidia-ml.so.1")


def load_cuda() -> Any | None:
    return _load("nvcuda.dll", "libcuda.so.1")


class _Session:
    def __init__(self, lib: Any) -> None:
        self.lib = lib
        self.active = False

    def __enter__(self) -> _Session:
        init = getattr(self.lib, "nvmlInit_v2", None) or self.lib.nvmlInit
        self.active = init() == _SUCCESS
        return self

    def __exit__(self, *_: object) -> None:
        if self.active:
            with contextlib.suppress(Exception):
                self.lib.nvmlShutdown()


def _text(call: Any, *args: Any) -> str | None:
    buffer = ctypes.create_string_buffer(_BUFFER)
    if call(*args, buffer, ctypes.c_uint(_BUFFER)) != _SUCCESS:
        return None
    value = buffer.value.decode("ascii", "replace").strip()
    return value or None


def _handle_by_uuid(lib: Any, uuid: str) -> Any | None:
    handle = ctypes.c_void_p()
    if lib.nvmlDeviceGetHandleByUUID(uuid.encode("ascii"), ctypes.byref(handle)) != _SUCCESS:
        return None
    return handle


def snapshot(lib: Any | None = None) -> NvmlSnapshot | None:
    lib = lib if lib is not None else load_nvml()
    if lib is None:
        return None
    try:
        with _Session(lib) as session:
            if not session.active:
                return None
            driver = _text(lib.nvmlSystemGetDriverVersion)
            if driver is None:
                return None
            cuda = ctypes.c_int()
            get_cuda = getattr(lib, "nvmlSystemGetCudaDriverVersion_v2", None)
            cuda_version = (
                int(cuda.value)
                if get_cuda is not None and get_cuda(ctypes.byref(cuda)) == _SUCCESS
                else None
            )
            count = ctypes.c_uint()
            get_count = getattr(lib, "nvmlDeviceGetCount_v2", None) or lib.nvmlDeviceGetCount
            if get_count(ctypes.byref(count)) != _SUCCESS:
                return None
            gpus = []
            get_handle = (
                getattr(lib, "nvmlDeviceGetHandleByIndex_v2", None)
                or lib.nvmlDeviceGetHandleByIndex
            )
            for index in range(int(count.value)):
                handle = ctypes.c_void_p()
                if get_handle(ctypes.c_uint(index), ctypes.byref(handle)) != _SUCCESS:
                    return None
                uuid = _text(lib.nvmlDeviceGetUUID, handle)
                name = _text(lib.nvmlDeviceGetName, handle)
                if uuid is None or name is None:
                    return None
                memory = _Memory()
                get_memory = getattr(lib, "nvmlDeviceGetMemoryInfo", None)
                measured = (
                    get_memory is not None and get_memory(handle, ctypes.byref(memory)) == _SUCCESS
                )
                total = int(memory.total) if measured and memory.total != NOT_AVAILABLE else None
                gpus.append(NvmlGpu(uuid, name, total))
            return NvmlSnapshot(driver, cuda_version, tuple(sorted(gpus, key=lambda g: g.uuid)))
    except Exception:
        return None


def free_memory(uuid: str, lib: Any | None = None) -> int | None:
    lib = lib if lib is not None else load_nvml()
    if lib is None:
        return None
    try:
        with _Session(lib) as session:
            handle = _handle_by_uuid(lib, uuid) if session.active else None
            memory = _Memory()
            if handle is None or lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)):
                return None
            return int(memory.free) if memory.free != NOT_AVAILABLE else None
    except Exception:
        return None


def _used_in_session(lib: Any, uuid: str, pid: int) -> int | None:
    handle = _handle_by_uuid(lib, uuid)
    call = getattr(lib, "nvmlDeviceGetComputeRunningProcesses_v3", None)
    if handle is None or call is None:
        return None
    entries = (_ProcessV3 * 64)()
    count = ctypes.c_uint(64)
    if call(handle, ctypes.byref(count), entries) != _SUCCESS:
        return None
    for entry in entries[: int(count.value)]:
        if int(entry.pid) == pid:
            used = int(entry.usedGpuMemory)
            return None if used in (NOT_AVAILABLE, 0) else used
    return None


class UsageSampler:
    """One NVML session for repeated reads of this process's VRAM on one GPU."""

    def __init__(self, uuid: str, lib: Any | None = None) -> None:
        self.uuid = uuid
        self.lib = lib if lib is not None else load_nvml()
        self.session: _Session | None = None

    def __enter__(self) -> UsageSampler:
        if self.lib is not None:
            try:
                self.session = _Session(self.lib).__enter__()
            except Exception:
                self.session = None
        return self

    def read(self) -> int | None:
        if self.session is None or not self.session.active:
            return None
        try:
            return _used_in_session(self.lib, self.uuid, os.getpid())
        except Exception:
            return None

    def __exit__(self, *exc: object) -> None:
        if self.session is not None:
            self.session.__exit__()


def process_used_memory(uuid: str, pid: int | None = None, lib: Any | None = None) -> int | None:
    """GPU memory this process uses on that GPU, or ``None`` (e.g. WDDM reports N/A)."""
    lib = lib if lib is not None else load_nvml()
    if lib is None:
        return None
    pid = os.getpid() if pid is None else pid
    try:
        with _Session(lib) as session:
            handle = _handle_by_uuid(lib, uuid) if session.active else None
            call = getattr(lib, "nvmlDeviceGetComputeRunningProcesses_v3", None)
            if handle is None or call is None:
                return None
            entries = (_ProcessV3 * 64)()
            count = ctypes.c_uint(64)
            if call(handle, ctypes.byref(count), entries) != _SUCCESS:
                return None
            for entry in entries[: int(count.value)]:
                if int(entry.pid) == pid:
                    used = int(entry.usedGpuMemory)
                    return None if used in (NOT_AVAILABLE, 0) else used
            return None
    except Exception:
        return None


def cuda_ordinal_uuid(ordinal: int, lib: Any | None = None) -> str | None:
    """UUID (NVML ``GPU-...`` form) of the CUDA driver API ordinal in *this* process."""
    lib = lib if lib is not None else load_cuda()
    if lib is None or ordinal < 0:
        return None
    try:
        if lib.cuInit(0) != _SUCCESS:
            return None
        device = ctypes.c_int()
        if lib.cuDeviceGet(ctypes.byref(device), ctypes.c_int(ordinal)) != _SUCCESS:
            return None
        raw = (ctypes.c_ubyte * 16)()
        call = getattr(lib, "cuDeviceGetUuid_v2", None) or lib.cuDeviceGetUuid
        if call(raw, device) != _SUCCESS:
            return None
        h = bytes(raw).hex()
        return f"GPU-{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    except Exception:
        return None
