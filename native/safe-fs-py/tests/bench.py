"""E-C2a PoC load measurement (synthetic data only).

Measures, through the installed wheel, a 32 MiB bounded read and a 128 MiB
exclusive write + flush, and prints one JSON line. Usage:
    python bench.py [work_dir]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import utteran_safe_fs_poc as sf


def rss_peak_mib() -> float | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024 if sys.platform != "darwin" else peak / (1024 * 1024)


def main() -> None:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    with tempfile.TemporaryDirectory(prefix="safefs-bench-", dir=base) as d:
        root = Path(d).resolve()
        read_src = os.urandom(1 << 20) * 32  # 32 MiB
        (root / "src.json").write_bytes(read_src)
        write_data = os.urandom(1 << 20) * 128  # 128 MiB
        rss0 = rss_peak_mib()
        with sf.DirChain.open(str(root)) as ch:
            t0 = time.perf_counter()
            got = ch.read_file("src.json", 32 << 20)
            t1 = time.perf_counter()
            ch.write_new_file("out.bin", write_data)
            t2 = time.perf_counter()
        assert hashlib.sha256(got).digest() == hashlib.sha256(read_src).digest()
        assert (root / "out.bin").stat().st_size == len(write_data)
        print(
            json.dumps(
                {
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                    "read_32mib_s": round(t1 - t0, 4),
                    "write_flush_128mib_s": round(t2 - t1, 4),
                    "rss_peak_mib_before": rss0,
                    "rss_peak_mib_after": rss_peak_mib(),
                }
            )
        )


if __name__ == "__main__":
    main()
