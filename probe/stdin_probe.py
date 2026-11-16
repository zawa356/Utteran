"""Windows probe: does a thread blocked reading the stdin pipe stall numpy import?

Each variant runs in its own child (python -I) with stdin=PIPE that the parent never
writes to, so a reader thread stays blocked in a synchronous read. Bounded timeouts,
child killed on timeout. faulthandler dumps the stuck stack to stderr.
"""
import subprocess, sys, time

CHILD = r'''
import faulthandler, os, sys, threading, time
faulthandler.dump_traceback_later(15, exit=False, file=sys.stderr)
mode = sys.argv[1]

def reader(stream):
    def run():
        try:
            stream.readline()
        except Exception as exc:
            print("reader error", type(exc).__name__, flush=True)
    t = threading.Thread(target=run, daemon=True); t.start(); time.sleep(0.5)

def isolate():
    import msvcrt, ctypes
    fd = os.dup(0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0); os.close(null)
    ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))
    return os.fdopen(fd, "rb", buffering=0)

t0 = time.perf_counter()
if mode == "baseline_no_thread":
    import numpy
elif mode == "thread_then_numpy":
    reader(sys.stdin.buffer); import numpy
elif mode == "numpy_then_thread":
    import numpy; reader(sys.stdin.buffer)
elif mode == "isolated_thread_then_numpy":
    reader(isolate()); import numpy
elif mode == "thread_then_fstat0":
    reader(sys.stdin.buffer); os.fstat(0)
elif mode == "thread_then_getfiletype":
    import ctypes
    reader(sys.stdin.buffer)
    k = ctypes.windll.kernel32
    k.GetFileType(k.GetStdHandle(-10))
elif mode == "isolated_thread_then_getfiletype":
    import ctypes
    reader(isolate())
    k = ctypes.windll.kernel32
    k.GetFileType(k.GetStdHandle(-10))
elif mode == "thread_then_popen_default_stdin":
    reader(sys.stdin.buffer)
    import subprocess
    subprocess.run(["cmd", "/c", "exit 0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("done %.2f" % (time.perf_counter() - t0), flush=True)
os._exit(0)
'''

MODES = [
    "baseline_no_thread", "thread_then_numpy", "numpy_then_thread",
    "isolated_thread_then_numpy", "thread_then_fstat0", "thread_then_getfiletype",
    "isolated_thread_then_getfiletype", "thread_then_popen_default_stdin",
]
failed = 0
for mode in MODES:
    for attempt in range(2):
        start = time.perf_counter()
        child = subprocess.Popen([sys.executable, "-I", "-c", CHILD, mode],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = child.communicate(timeout=40)
            state = "exit"
        except subprocess.TimeoutExpired:
            child.kill(); out, err = child.communicate()
            state = "TIMEOUT"
        elapsed = time.perf_counter() - start
        print(f"[{mode} #{attempt}] {state} {elapsed:.1f}s out={out.decode(errors='replace').strip()!r}")
        tail = err.decode(errors="replace").strip().splitlines()
        if tail:
            print("   stderr:\n      " + "\n      ".join(tail[-25:]))
print("probe finished")
