"""Generate privacy-safe acceptance clips and malformed fixtures from the real meeting file."""

from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import statistics
import subprocess
from pathlib import Path


def _probe_duration(ffprobe: Path, source: Path) -> float:
    completed = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def _window_score(
    ffmpeg: Path, source: Path, start: float, duration: float = 180.0
) -> dict[str, float]:
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    samples = array.array("h")
    samples.frombytes(completed.stdout)
    per_second: list[float] = []
    for offset in range(0, len(samples), 8000):
        block = samples[offset : offset + 8000]
        if block:
            per_second.append(math.sqrt(sum(value * value for value in block) / len(block)))
    if not per_second:
        return {"start": start, "active_ratio": 0.0, "variation": 0.0, "score": 0.0}
    median = statistics.median(per_second)
    threshold = max(120.0, median * 0.25)
    active = [value for value in per_second if value >= threshold]
    active_ratio = len(active) / len(per_second)
    variation = statistics.pstdev(active) / statistics.fmean(active) if active else 0.0
    return {
        "start": start,
        "active_ratio": active_ratio,
        "variation": variation,
        "score": active_ratio + min(variation, 2.0) * 0.1,
    }


def _run_ffmpeg(ffmpeg: Path, arguments: list[str]) -> None:
    subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *arguments], check=True
    )


def _generate_clips(ffmpeg: Path, source: Path, output: Path, selected_start: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ffmpeg,
        ["-ss", "0", "-i", str(source), "-t", "30", "-c", "copy", str(output / "clip_30s.mp4")],
    )
    _run_ffmpeg(
        ffmpeg,
        [
            "-ss",
            f"{selected_start:.3f}",
            "-i",
            str(source),
            "-t",
            "180",
            "-c",
            "copy",
            str(output / "clip_03m.mp4"),
        ],
    )
    _run_ffmpeg(
        ffmpeg,
        [
            "-ss",
            f"{selected_start:.3f}",
            "-i",
            str(source),
            "-t",
            "600",
            "-c",
            "copy",
            str(output / "clip_10m.mp4"),
        ],
    )
    _run_ffmpeg(
        ffmpeg,
        [
            "-i",
            str(output / "clip_03m.mp4"),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output / "clip_03m.wav"),
        ],
    )
    _run_ffmpeg(
        ffmpeg,
        [
            "-i",
            str(output / "clip_03m.mp4"),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output / "clip_03m.m4a"),
        ],
    )


def _generate_malformed(output: Path) -> None:
    source = output / "clip_03m.mp4"
    with source.open("rb") as reader, (output / "broken.mp4").open("wb") as writer:
        writer.write(reader.read(65536))
    (output / "empty.mp4").touch()
    (output / "notmedia.txt").write_text("utteran acceptance non-media fixture\n", encoding="utf-8")


def _prepare_batch(output: Path) -> None:
    batch = output / "batch"
    nested = batch / "nested"
    batch.mkdir(exist_ok=True)
    nested.mkdir(exist_ok=True)
    for name in ("clip_30s.mp4", "clip_03m.m4a", "clip_03m.wav", "broken.mp4", "notmedia.txt"):
        shutil.copy2(output / name, batch / name)
    shutil.copy2(output / "clip_30s.mp4", nested / "nested_clip.mp4")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    args = parser.parse_args()

    sources = sorted(
        path for path in args.input_dir.iterdir() if path.is_file() and path.name != ".gitkeep"
    )
    if len(sources) != 1:
        raise RuntimeError(f"expected exactly one input file, found {len(sources)}")
    source = sources[0]
    duration = _probe_duration(args.ffprobe, source)
    window_duration = 180.0
    candidates = [
        max(0.0, min(duration - window_duration, duration * fraction))
        for fraction in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85)
    ]
    scores = [_window_score(args.ffmpeg, source, start) for start in candidates]
    selected = max(scores, key=lambda item: (item["score"], item["start"]))
    _generate_clips(args.ffmpeg, source, args.output_dir, selected["start"])
    _generate_malformed(args.output_dir)
    _prepare_batch(args.output_dir)
    metadata = {
        "source_name": source.name,
        "source_size_bytes": source.stat().st_size,
        "source_duration_seconds": duration,
        "selection_method": "highest active-audio ratio plus RMS variation across six windows",
        "selected_start_seconds": selected["start"],
        "candidate_statistics": scores,
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
