"""Windows probe 2: real spawn flags (CREATE_NO_WINDOW|NEW_PROCESS_GROUP + Job Object),
worker-like stdio (protocol fd dup, fd 1/2 -> NUL), real cpu-profile deps, and the real
``python -I -m utteran.worker`` with a synthetic request. Bounded; children killed."""
from __future__ import annotations

import json, os, struct, subprocess, sys, tempfile, threading, time, wave
from pathlib import Path

from utteran_gui.processes import ProcessSupervisor, build_popen_kwargs, kill_process_tree

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 90.0

CHILD = r'''
import faulthandler, os, sys, threading, time
mode, dump = sys.argv[1], sys.argv[2]
proto = os.fdopen(os.dup(1), "w", buffering=1)
null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1); os.dup2(null, 2)
fh = open(dump, "w"); faulthandler.dump_traceback_later(30, exit=False, file=fh)
def reader(stream):
    threading.Thread(target=lambda: stream.readline(), daemon=True).start(); time.sleep(0.5)
def isolate():
    import msvcrt, ctypes
    fd = os.dup(0); n = os.open(os.devnull, os.O_RDONLY); os.dup2(n, 0); os.close(n)
    ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))
    return os.fdopen(fd, "rb", buffering=0)
t0 = time.perf_counter()
target = mode.split(":")[1]
def load():
    if target == "numpy":
        import numpy
    else:
        from faster_whisper import WhisperModel
order = mode.split(":")[0]
if order == "thread_first": reader(sys.stdin.buffer); load()
elif order == "load_first": load(); reader(sys.stdin.buffer)
elif order == "isolated": reader(isolate()); load()
elif order == "no_thread": load()
proto.write("done %.2f\n" % (time.perf_counter() - t0)); proto.flush()
os._exit(0)
'''


def run(command, env, payload=None, label=""):
    kwargs = build_popen_kwargs(cwd=ROOT, env=env)
    kwargs.update(stdin=subprocess.PIPE, text=False, encoding=None, errors=None, bufsize=-1)
    sup = ProcessSupervisor()
    start = time.perf_counter()
    proc = sup.popen(command, **kwargs)
    lines: list[str] = []
    def pump():
        for raw in proc.stdout:
            lines.append(f"{time.perf_counter() - start:6.1f}s " + raw.decode(errors="replace").strip()[:300])
    t = threading.Thread(target=pump, daemon=True); t.start()
    if payload is not None:
        proc.stdin.write(payload); proc.stdin.flush()  # stdin stays OPEN, like WorkerClient
    try:
        proc.wait(timeout=TIMEOUT); state = f"exit={proc.returncode}"
    except subprocess.TimeoutExpired:
        state = "TIMEOUT"; kill_process_tree(proc)
    t.join(5)
    sup.shutdown()
    print(f"[{label}] {state} after {time.perf_counter() - start:.1f}s")
    for line in lines[-12:]:
        print("    " + line)
    return state


def child_cases(env):
    for target in ("numpy", "fw"):
        for order in ("no_thread", "thread_first", "load_first", "isolated"):
            for attempt in range(2):
                with tempfile.TemporaryDirectory() as d:
                    dump = os.path.join(d, "dump.txt")
                    state = run([sys.executable, "-I", "-c", CHILD, f"{order}:{target}", dump], env,
                                label=f"child {order}:{target} #{attempt}")
                    if os.path.exists(dump):
                        text = Path(dump).read_text(errors="replace").strip()
                        if text:
                            print("    faulthandler:\n      " + "\n      ".join(text.splitlines()[:30]))


def synthetic_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(b"\0\0" * 16000)


def worker_case(env, cancel_after: float | None, label: str):
    from utteran_contracts.worker import REQUEST_LIMIT, WorkerCancel, WorkerRequest
    from utteran_contracts.worker_framing import encode_record
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        model = root / "model"; model.mkdir()
        for n in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
            (model / n).write_text("{}" if n.endswith(".json") else "x", encoding="utf-8")
        (root / "out").mkdir(); (root / "jobs").mkdir()
        wav = root / "input.wav"; synthetic_wav(wav)
        settings = json.loads((ROOT / "docs/architecture/phase-a/examples.json").read_text("utf-8"))["preset"]["settings"]
        settings["output"]["formats"] = ["srt", "json", "md"]
        req = WorkerRequest(request_id="req-probe", job_id="job-probe", attempt_id="attempt-probe",
            profile="cpu", input_path=str(wav), settings=settings,
            context=dict(base_config={"general": {"output_dir": str(root / "out"), "job_dir": str(root / "jobs")}},
                asr_backend="faster-whisper", asr_model="large-v3-turbo", asr_model_dir=str(model),
                asr_device="cpu", diarization_device="cpu", compute_type="int8"))
        payload = encode_record(req, REQUEST_LIMIT)
        if cancel_after is not None:
            cancel = WorkerCancel(request_id="req-probe", job_id="job-probe", attempt_id="attempt-probe")
            def later():
                time.sleep(cancel_after)
                try:
                    proc_stdin[0].write(encode_record(cancel, REQUEST_LIMIT)); proc_stdin[0].flush()
                except Exception as exc:
                    print("    cancel write failed", type(exc).__name__)
        e = dict(env, UTTERAN_PROFILE="cpu")
        kwargs = build_popen_kwargs(cwd=ROOT, env=e)
        kwargs.update(stdin=subprocess.PIPE, text=False, encoding=None, errors=None, bufsize=-1)
        sup = ProcessSupervisor(); start = time.perf_counter()
        proc = sup.popen([sys.executable, "-I", "-m", "utteran.worker", "--protocol", "1"], **kwargs)
        proc_stdin = [proc.stdin]
        lines = []
        def pump():
            for raw in proc.stdout:
                lines.append(f"{time.perf_counter() - start:6.1f}s " + raw.decode(errors="replace").strip()[:200])
        threading.Thread(target=pump, daemon=True).start()
        proc.stdin.write(payload); proc.stdin.flush()
        if cancel_after is not None:
            threading.Thread(target=later, daemon=True).start()
        try:
            proc.wait(timeout=TIMEOUT); state = f"exit={proc.returncode}"
        except subprocess.TimeoutExpired:
            state = "TIMEOUT"; kill_process_tree(proc)
        time.sleep(1); sup.shutdown()
        print(f"[{label}] {state} after {time.perf_counter() - start:.1f}s")
        for line in lines:
            print("    " + line)


if __name__ == "__main__":
    env = dict(os.environ)
    print("python", sys.version.split()[0])
    child_cases(env)
    for attempt in range(2):
        worker_case(env, None, f"real worker #{attempt}")
    worker_case(env, 3.0, "real worker + cancel at 3s")
    print("probe2 finished")
