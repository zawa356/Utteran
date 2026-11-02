"""Real bounded metadata-reader children; no models, probes, or real secrets."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import tracemalloc
from functools import partial
from pathlib import Path
from typing import cast

import pytest

from utteran_contracts.capabilities import InventoryRefreshRequest
from utteran_gui.capability_inventory import CapabilityInventoryAdapter
from utteran_gui.capability_transport import (
    STDERR_LIMIT,
    STDOUT_LIMIT,
    BridgeReadError,
    bridge_environment,
    read_bridge,
)
from utteran_gui.cli import CliAdapter, ProfileInfo

# The response reflects private settings read from stdin, never from argv/env.
_READ_REQUEST = """
import sys, json
request = json.loads(sys.stdin.buffer.read())
result = dict(schema_version=1, reason_code='supported', inventory=dict(
 schema_version=1, revision=request['revision'], settings=request['settings'],
 execution_path=request['execution_path'], observed_at='2026-11-02T00:00:00Z',
 candidates=[], exclusions=[]))
"""
_WRITE_RESPONSE = "sys.stdout.buffer.write(json.dumps(result).encode('utf-8'))\n"


@pytest.fixture
def request_dto(worker_request):
    return InventoryRefreshRequest(
        schema_version=1,
        profile_id="cpu",
        revision=1,
        settings=worker_request.settings,
        execution_path="cli",
    )


@pytest.fixture
def cli(tmp_path, monkeypatch):
    cli = CliAdapter(tmp_path)
    root = tmp_path / "profile"
    for relative in ("bin/python", "Scripts/python.exe"):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    monkeypatch.setattr(
        cli,
        "profile_info",
        lambda _: ProfileInfo(
            "cpu",
            root,
            True,
            root / "utteran",
            compatible=True,
        ),
    )
    return cli


class Peer:
    def __init__(self, tmp_path, script):
        self.script = tmp_path / "bridge_peer.py"
        self.script.write_text(script, encoding="utf-8")
        self.children = []
        self.calls = []

    def popen(self, command, *, supervisor, **kwargs):
        self.calls.append((command, kwargs))
        child = supervisor.popen([sys.executable, "-B", str(self.script)], **kwargs)
        self.children.append(child)
        return cast(subprocess.Popen[bytes], child)

    @property
    def runner(self):
        return partial(read_bridge, popen_factory=self.popen)

    def assert_reaped(self):
        assert self.children and all(child.returncode is not None for child in self.children)
        for child in self.children:
            assert all(pipe.closed for pipe in (child.stdin, child.stdout, child.stderr))


def test_large_valid_json_under_limit_is_not_truncated(request_dto, cli, tmp_path):
    script = _READ_REQUEST + "sys.stdout.buffer.write(b' ' * (7 * 1024 * 1024))\n" + _WRITE_RESPONSE
    peer = Peer(tmp_path, script)
    adapter = CapabilityInventoryAdapter(cli, runner=peer.runner)
    result = adapter.refresh(request_dto, timeout=10.0)
    assert result.inventory is not None and result.inventory.settings == request_dto.settings
    peer.assert_reaped()
    command, kwargs = peer.calls[0]
    assert Path(command[0]).is_absolute()
    assert command[1:] == ["-I", "-B", "-m", "utteran.capabilities.bridge", "refresh"]
    assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.PIPE
    assert "PRIVATE_PROMPT" not in str(command)


@pytest.mark.parametrize(
    "channel,reason",
    [
        (1, "bridge_stdout_limit"),
        (2, "bridge_stderr_limit"),
    ],
)
def test_flood_is_stopped_before_full_capture(request_dto, cli, tmp_path, channel, reason):
    # A 256 MiB finite flood must be interrupted at the configured byte budget.
    # The memory assertion catches capture_output/communicate accumulation.
    script = (
        _READ_REQUEST
        + f"""
import os
for _ in range(32768):
 os.write({channel}, b'x' * 8192)
"""
    )
    peer = Peer(tmp_path, script)
    tracemalloc.start()
    try:
        result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(
            request_dto, timeout=10
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.inventory is None and result.reason_code == reason
    assert peak < 32 * 1024 * 1024
    peer.assert_reaped()


def test_both_pipes_are_drained_and_stderr_is_discarded(request_dto, cli, tmp_path):
    script = (
        _READ_REQUEST
        + """
import os, threading
thread = threading.Thread(target=lambda: os.write(2, b'PRIVATE_STDERR' * 3000))
thread.start()
sys.stdout.buffer.write(b' ' * (1024 * 1024))
thread.join()
"""
        + _WRITE_RESPONSE
    )
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=10)
    assert result.inventory is not None and "PRIVATE_STDERR" not in repr(result)
    peer.assert_reaped()


def test_stderr_flood_concurrent_with_stdout_cannot_deadlock(request_dto, cli, tmp_path):
    script = (
        _READ_REQUEST
        + """
import os, threading, time
thread = threading.Thread(target=lambda: [os.write(2, b'x'*8192) for _ in range(32768)])
thread.start()
sys.stdout.buffer.write(b' ' * (1024 * 1024))
time.sleep(30)
"""
    )
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=5)
    assert result.inventory is None and result.reason_code == "bridge_stderr_limit"
    peer.assert_reaped()


def test_valid_json_prefix_beyond_limit_is_never_accepted(request_dto, cli, tmp_path):
    script = _READ_REQUEST + _WRITE_RESPONSE + "sys.stdout.buffer.write(b' ' * (9*1024*1024))\n"
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=10)
    assert result.inventory is None and result.reason_code == "bridge_stdout_limit"
    peer.assert_reaped()


@pytest.mark.parametrize(
    "script,reason",
    [
        ("import time; time.sleep(30)", "probe_timeout"),
        (_READ_REQUEST + "sys.stdout.write('{')", "bridge_invalid"),
        (_READ_REQUEST + "sys.stdout.buffer.write(b'\\xff')", "bridge_invalid"),
        (_READ_REQUEST + "sys.stdout.write('PRIVATE_TRACE'); sys.exit(7)", "bridge_unavailable"),
        (_READ_REQUEST + "sys.exit(0)", "bridge_invalid"),
        ("import sys; sys.exit(1)", "bridge_unavailable"),
    ],
)
def test_child_faults_are_fixed_reasons_and_reaped(
    request_dto, cli, tmp_path, script, reason, caplog
):
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=1.5)
    assert result.inventory is None and result.reason_code == reason
    assert "PRIVATE_TRACE" not in str(result) + caplog.text
    peer.assert_reaped()


def test_descendant_holding_pipes_is_terminated_after_parent_exit(request_dto, cli, tmp_path):
    pidfile = tmp_path / "descendant.pid"
    script = (
        _READ_REQUEST
        + f"""
import subprocess, pathlib
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
pathlib.Path({str(pidfile)!r}).write_text(str(child.pid), encoding='utf-8')
"""
    )
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=2)
    assert result.inventory is None and result.reason_code == "probe_timeout"
    peer.assert_reaped()
    pid = int(pidfile.read_text(encoding="utf-8"))
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                assert kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel32.CloseHandle(handle)
    else:
        # Container PID 1 may leave a dead descendant as a zombie temporarily.
        deadline = time.monotonic() + 3
        stat = Path(f"/proc/{pid}/stat")
        while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not stat.exists() or stat.read_text().split()[2] == "Z"


def test_environment_is_allowlisted_without_consulting_session_token(
    request_dto, cli, tmp_path, monkeypatch, caplog
):
    secret = "BRIDGE_SECRET_SENTINEL"
    for key in (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OPENAI_API_KEY",
        "UTTERAN_SESSION_TOKEN",
        "RANDOM_PRIVATE_VALUE",
        "HTTP_PROXY",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(key, secret)

    def forbidden(*args):
        raise AssertionError("legacy environment/session token builder must not be called")

    monkeypatch.setattr(cli, "environment", forbidden)
    script = (
        _READ_REQUEST
        + """
import os
assert 'BRIDGE_SECRET_SENTINEL' not in os.environ.values()
assert os.environ['UTTERAN_PROFILE'] == 'cpu'
assert os.environ['HF_HUB_OFFLINE'] == '1'
if os.name == 'nt':
 assert os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
 assert os.environ.get('TEMP') or os.environ.get('TMP')
else:
 assert os.environ.get('HOME')
"""
        + _WRITE_RESPONSE
    )
    peer = Peer(tmp_path, script)
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto, timeout=10)
    assert result.inventory is not None
    command, kwargs = peer.calls[0]
    assert secret not in str(command) + str(kwargs["env"]) + repr(result) + caplog.text
    peer.assert_reaped()


def test_environment_preserves_os_runtime_and_data_roots():
    source = {
        name: "os-value"
        for name in (
            "SystemRoot",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "HOME",
            "LD_LIBRARY_PATH",
            "DYLD_LIBRARY_PATH",
            "UTTERAN_DATA_ROOT",
            "XDG_CACHE_HOME",
        )
    }
    source.update(hf_token="SECRET", SECRET_SESSION_TOKEN="SECRET", AWS_SESSION_TOKEN="SECRET")
    env = bridge_environment("cuda", source)
    assert all(env[name] == "os-value" for name in source if source[name] == "os-value")
    assert "SECRET" not in env.values() and env["UTTERAN_PROFILE"] == "cuda"


def test_limits_and_timeout_validated_before_spawning(tmp_path):
    for timeout in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(BridgeReadError, match="bridge_invalid"):
            read_bridge(["unused"], cwd=tmp_path, env={}, input=b"", timeout=timeout)
    assert STDOUT_LIMIT == 8 * 1024 * 1024 and STDERR_LIMIT == 64 * 1024


def test_fixed_errors_and_profile_missing_are_safe(request_dto, cli, monkeypatch):
    monkeypatch.setattr(
        cli,
        "profile_info",
        lambda _: ProfileInfo("cpu", Path("/missing"), False, Path("/missing/python")),
    )
    result = CapabilityInventoryAdapter(cli).refresh(request_dto)
    assert result.inventory is None and result.reason_code == "profile_missing"
    error = BridgeReadError("bridge_stdout_limit")
    assert str(error) == "bridge_stdout_limit"
    assert error.args == ("bridge_stdout_limit",)


def test_blocked_stdin_writer_is_bounded_and_reaped(tmp_path):
    peer = Peer(tmp_path, "import time; time.sleep(30)")
    with pytest.raises(BridgeReadError, match="probe_timeout"):
        peer.runner(
            [sys.executable],
            cwd=tmp_path,
            env=bridge_environment("cpu"),
            input=b"x" * (512 * 1024),
            timeout=1.5,
        )
    peer.assert_reaped()


def test_start_failure_does_not_expose_os_error(tmp_path):
    with pytest.raises(BridgeReadError) as error:
        read_bridge(
            [str(tmp_path / "PRIVATE_EXECUTABLE")],
            cwd=tmp_path,
            env=bridge_environment("cpu"),
            input=b"",
            timeout=2,
        )
    assert error.value.code == "bridge_unavailable"
    assert "PRIVATE" not in str(error.value)


def test_real_missing_bridge_preserves_safe_unavailable(request_dto, cli, tmp_path):
    peer = Peer(tmp_path, "import sys; sys.stderr.write('No module named PRIVATE'); sys.exit(1)")
    result = CapabilityInventoryAdapter(cli, runner=peer.runner).refresh(request_dto)
    assert result.reason_code == "bridge_unavailable" and result.inventory is None
    peer.assert_reaped()


def test_transport_is_opt_in_and_does_not_run_on_gui_import():
    code = """
import sys

def audit(event, args):
 if event.startswith(('subprocess.', 'os.system', 'os.fork')):
  raise AssertionError('implicit bridge launch')
sys.addaudithook(audit)
import utteran_gui.api
import utteran_gui.capability_inventory
assert not any(n in sys.modules for n in (
 'utteran','torch','openvino','faster_whisper','ctranslate2'))
"""
    child = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=20)
    assert child.returncode == 0, child.stderr
