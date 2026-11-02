"""Synthetic peer for IPC fault injection; never installed as a worker backend."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]


def send(kind, sequence, **values):
    event = dict(
        type=kind,
        sequence=sequence,
        schema_version=1,
        protocol_version=1,
        request_id=None,
        job_id=None,
        attempt_id=None,
    )
    event.update(values)
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if mode == "missing":
    sys.stderr.write("No module named utteran.worker\n")
    sys.exit(1)
if mode == "hang":
    time.sleep(30)
if mode == "version":
    send("hello", 0, profile="cpu", protocol_version=2)
    time.sleep(30)
if mode == "unknown":
    send("hello", 0, profile="cpu", PRIVATE_PROMPT="PRIVATE_PROMPT")
    time.sleep(30)
if mode == "oversized":
    sys.stdout.write("x" * (1024 * 1024 + 2) + "\n")
    sys.stdout.flush()
    time.sleep(30)
if mode == "invalid_utf8":
    sys.stdout.buffer.write(b"\xff\n")
    sys.stdout.flush()
    time.sleep(30)
if mode == "malformed":
    sys.stdout.write("{invalid}\n")
    sys.stdout.flush()
    time.sleep(30)
if mode in {"tree", "orphan"}:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(sys.argv[2]).write_text(str(child.pid))
    if mode == "orphan":
        os._exit(0)
    time.sleep(30)
send("hello", 0, profile="cpu")
if mode == "no_read":
    time.sleep(30)
request = json.loads(sys.stdin.buffer.readline())
identity = {key: request[key] for key in ("request_id", "job_id", "attempt_id")}
if mode == "no_read":
    time.sleep(30)
if mode == "validate_hang":
    send("accepted", 1, **identity)
    time.sleep(0.2)
    cancel = json.loads(sys.stdin.buffer.readline())
    send("cancel_acknowledged", 2, **identity)
    send("error", 3, code="cancelled", **identity)
    sys.exit(1)
if mode == "empty":
    sys.exit(0)
if mode == "crash":
    os._exit(7)
send("accepted", 1, **identity)
if mode in {"cancel", "cancel_crash"}:
    send("progress", 2, stage="job", state="validated", fraction=None, **identity)
    cancel = json.loads(sys.stdin.buffer.readline())
    send("cancel_acknowledged", 3, **identity)
    if mode == "cancel_crash":
        os._exit(7)
    send("error", 4, code="cancelled", **identity)
    sys.exit(1)
if mode == "flood":
    sys.stderr.write("PRIVATE_PROMPT /private/input hf_synthetic123 " * 50000)
    sys.stderr.flush()
if mode == "wrong_id":
    identity["attempt_id"] = "wrong"
if mode in {"error", "wrong_id", "flood", "duplicate", "zero_error"}:
    send("error", 2, code="core_failure", **identity)
    if mode == "duplicate":
        send("error", 3, code="core_failure", **identity)
    sys.exit(0 if mode == "zero_error" else 1)
# Success evidence is synthetic, but checked by the real parent implementation.
root = Path(request["context"]["base_config"]["general"]["output_dir"])
root.mkdir(parents=True, exist_ok=True)
outputs = []
for index, fmt in enumerate(request["settings"]["output"]["formats"]):
    if mode == "wrong_formats":
        fmt = request["settings"]["output"]["formats"][0]
    path = root / (f"synthetic{index}." + fmt)
    path.write_text("synthetic result")
    outputs.append(path)
core_id = "0123456789abcdef"
manifest = Path(request["context"]["base_config"]["general"]["job_dir"]) / core_id / "manifest.json"
manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(
    json.dumps(
        dict(
            version=1,
            job_id=core_id,
            input=dict(path=request["input_path"]),
            stages={
                s: dict(status="done", artifacts=[str(p) for p in outputs] if s == "export" else [])
                for s in ("audio", "asr", "diarization", "merge", "export")
            },
        )
    )
)


def artifact(path):
    return dict(
        path=str(path),
        size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


send(
    "completed",
    2,
    core_job_id=core_id,
    artifacts=[artifact(p) for p in outputs],
    manifest=artifact(manifest),
    **identity,
)
if mode == "success_crash":
    sys.exit(7)
if mode == "terminal_hang":
    time.sleep(30)
