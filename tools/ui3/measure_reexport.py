"""E-C: measure parse/render time and peak memory of re-export sources (synthetic only).

Usage: ``uv run --no-sync python tools/ui3/measure_reexport.py [MiB ...]`` (sources) or ``...
measure_reexport.py --store [records ...]`` (physical size of the record store). For each source
size a fresh subprocess builds a JSON v1 document of about that size with the shared renderer
(the JSON exporter's own layout), then times ``parse_json_v1`` (with SHA-256) and the rendering
of all five formats, and reports the peak RSS. No user data; nothing is written to disk.
"""

from __future__ import annotations

import hashlib
import resource
import subprocess
import sys
import time

from utteran_contracts.transcript_export import (
    FORMATS,
    EngineView,
    RenderOptions,
    SegmentView,
    TranscriptView,
    WordView,
    parse_json_v1,
    render_bytes,
    render_json,
)


def _document(target: int) -> bytes:
    words = tuple(WordView(i * 0.31, i * 0.31 + 0.3, "日本語の単語", 0.912345) for i in range(12))
    text = "これは合成された長めの発話です。" * 3
    engine = EngineView("synthetic", "synthetic-model", "cpu")

    def view(count: int) -> TranscriptView:
        return TranscriptView(
            "/synthetic/input.wav",
            count * 3.5,
            engine,
            None,
            "2026-11-25T00:00:00+09:00",
            tuple(
                SegmentView(i * 3.5, i * 3.5 + 3, text, f"SPEAKER_{i % 8:02d}", "high", words)
                for i in range(count)
            ),
        )

    probe = len(render_json(view(1), RenderOptions()).encode())
    return render_json(view(max(1, target // (probe - 300))), RenderOptions()).encode()


def _child(mib: int) -> None:
    data = _document(mib * 1024 * 1024)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.perf_counter()
    hashlib.sha256(data).hexdigest()
    parsed, _ = parse_json_v1(data)
    parsed_at = time.perf_counter()
    labels = RenderOptions(speaker_labels={"SPEAKER_00": "A" * 256})
    total = sum(len(render_bytes(name, parsed, labels)) for name in FORMATS)
    done = time.perf_counter()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        len(data),
        len(parsed.segments),
        round(parsed_at - start, 2),
        round(done - parsed_at, 2),
        total,
        before // 1024,
        peak // 1024,
    )


def _store(count: int) -> None:
    """Fill a fresh re-export store with ``count`` completed records (worst-case labels)."""
    import tempfile
    from pathlib import Path

    from utteran_gui.reexport_store import ReexportStore, canonical

    labels = {f"SPEAKER_{i:02d}": "長" * 256 for i in range(8)}
    request = canonical(
        {
            "schema_version": 1,
            "source_sha256": "a" * 64,
            "formats": ["srt", "vtt", "json", "txt", "md"],
            "show_speaker": True,
            "srt_bom": False,
            "newline": "lf",
            "speaker_labels": labels,
            "output_parent": "/synthetic/" + "p" * 200,
        }
    )
    files = [{"format": f, "sha256": "b" * 64, "size": 123456} for f in FORMATS]
    with tempfile.TemporaryDirectory() as directory:
        store = ReexportStore(Path(directory) / "reexports.sqlite3")
        store.create_if_missing()
        for index in range(count):
            record, _ = store.begin(
                job_id=f"job_{index % 20:032x}",
                job_revision=3,
                key=f"measure-key-{index:012d}",
                digest="c" * 64,
                request=request,
                parent="/synthetic/" + "p" * 200,
                source_sha256="a" * 64,
                source_size=1000,
            )
            store.claim(record.id, "x" * 120)
            store.complete(record.id, files, True)
        physical = store.physical()
        size = (Path(directory) / "reexports.sqlite3").stat().st_size
        print(count, len(request.encode()), size, physical["pages"], physical["free_pages"])


def main() -> None:
    if sys.argv[1:2] == ["--store"]:
        print("records request_bytes file_bytes pages free_pages")
        for count in [int(x) for x in sys.argv[2:]] or [100, 1000, 2000]:
            _store(count)
        return
    if sys.argv[1:2] == ["--child"]:
        _child(int(sys.argv[2]))
        return
    sizes = [int(x) for x in sys.argv[1:]] or [1, 8, 16, 32, 64]
    print("source_bytes segments parse_s render5_s output_bytes rss_before_MiB rss_peak_MiB")
    for size in sizes:
        start = time.perf_counter()
        out = subprocess.run(
            [sys.executable, __file__, "--child", str(size)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(out, f"(wall {time.perf_counter() - start:.1f}s)")


if __name__ == "__main__":
    main()
