"""Validate acceptance artifacts using structure and statistics, never transcript content."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_SRT_TIME = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3}) --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
_VTT_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")
_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_COUNTED_CHARACTER = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_SPEAKER_PREFIX = re.compile(r"^[^:\r\n]{1,100}:\s+\S")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"JSON root is not an object: {path}")
    return value


def _latest_artifact(directory: Path, stem: str, extension: str) -> Path:
    candidates = [directory / f"{stem}.{extension}"]
    candidates.extend(sorted(directory.glob(f"{stem}_[0-9]*.{extension}")))
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise AssertionError(f"missing {extension} artifact for stem {stem}")
    return max(existing, key=lambda path: path.stat().st_mtime_ns)


def _validate_segments(payload: dict[str, Any], expected_duration: float | None) -> dict[str, Any]:
    for key in ("schema_version", "input", "processing", "speakers", "segments"):
        if key not in payload:
            raise AssertionError(f"missing JSON key: {key}")
    if payload["schema_version"] != 1:
        raise AssertionError("schema_version is not 1")
    segments = payload["segments"]
    if not isinstance(segments, list):
        raise AssertionError("segments is not a list")
    duration = float(payload["input"]["duration"])
    if expected_duration is not None and abs(duration - expected_duration) > 5.0:
        raise AssertionError(
            f"duration {duration:.3f}s differs from expected {expected_duration:.3f}s"
        )
    previous_start = -1.0
    empty_count = 0
    texts: list[str] = []
    coverage = 0.0
    for index, segment in enumerate(segments):
        start = float(segment["start"])
        end = float(segment["end"])
        if start < previous_start or start >= end or start < 0 or end > duration + 1.0:
            raise AssertionError(f"invalid segment timestamp at index {index}")
        previous_start = start
        coverage += end - start
        text = str(segment.get("text", "")).strip()
        texts.append(text)
        empty_count += not text
        for word_index, word in enumerate(segment.get("words", [])):
            word_start = float(word["start"])
            word_end = float(word["end"])
            if word_start < start - 0.05 or word_end > end + 0.05 or word_start > word_end:
                raise AssertionError(f"word outside segment at {index}:{word_index}")
    empty_ratio = empty_count / len(segments) if segments else 0.0
    if segments and empty_ratio >= 0.1:
        raise AssertionError(f"empty segment ratio too high: {empty_ratio:.3f}")
    max_repeat = 0
    current_repeat = 0
    previous_text: str | None = None
    for text in texts:
        current_repeat = current_repeat + 1 if text and text == previous_text else 1
        max_repeat = max(max_repeat, current_repeat)
        previous_text = text
    if max_repeat >= 5:
        raise AssertionError(f"identical text repeated consecutively {max_repeat} times")
    combined = "".join(texts)
    counted = _COUNTED_CHARACTER.findall(combined)
    japanese_ratio = (
        sum(bool(_JAPANESE.fullmatch(char)) for char in counted) / len(counted) if counted else 0
    )
    coverage_ratio = coverage / duration if duration > 0 else 0.0
    return {
        "duration_seconds": round(duration, 3),
        "segment_count": len(segments),
        "empty_ratio": round(empty_ratio, 6),
        "japanese_character_ratio": round(japanese_ratio, 6),
        "max_consecutive_duplicate_segments": max_repeat,
        "coverage_ratio": round(coverage_ratio, 6),
        "speaker_count": len(payload["speakers"]),
    }


def _validate_srt(path: Path, expect_bom: bool) -> int:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") != expect_bom:
        raise AssertionError(f"SRT BOM state did not match expectation: {path}")
    blocks = re.split(r"\r?\n\r?\n", raw.decode("utf-8-sig").strip()) if raw.strip() else []
    for index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index) or not _SRT_TIME.fullmatch(lines[1]):
            raise AssertionError(f"invalid SRT cue {index}")
    return len(blocks)


def _validate_vtt(path: Path) -> int:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("WEBVTT\n") and not text.startswith("WEBVTT\r\n"):
        raise AssertionError("missing WEBVTT header")
    return sum(bool(_VTT_TIME.fullmatch(line)) for line in text.splitlines())


def validate_formats(
    directory: Path,
    stem: str,
    expected_duration: float | None,
    expect_speakers: bool,
    expect_bom: bool,
) -> dict[str, Any]:
    """Validate five output formats and return content-free statistics."""
    paths = {
        extension: _latest_artifact(directory, stem, extension)
        for extension in ("srt", "vtt", "json", "txt", "md")
    }
    stats = _validate_segments(_load_json(paths["json"]), expected_duration)
    cue_count = _validate_srt(paths["srt"], expect_bom)
    vtt_count = _validate_vtt(paths["vtt"])
    if cue_count != stats["segment_count"] or vtt_count != stats["segment_count"]:
        raise AssertionError("subtitle cue counts do not match JSON segments")
    txt_lines = [line for line in paths["txt"].read_text(encoding="utf-8-sig").splitlines() if line]
    if expect_speakers and txt_lines and not all(_SPEAKER_PREFIX.match(line) for line in txt_lines):
        raise AssertionError("TXT speaker prefix is missing")
    markdown = paths["md"].read_text(encoding="utf-8-sig")
    if "## メタ情報" not in markdown or "## 文字起こし" not in markdown:
        raise AssertionError("Markdown sections are missing")
    stats["files"] = {name: path.name for name, path in paths.items()}
    stats["srt_bom"] = expect_bom
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def validate_json(
    path: Path,
    expected_duration: float | None,
    min_segments: int | None,
    max_segments: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> dict[str, Any]:
    stats = _validate_segments(_load_json(path), expected_duration)
    if min_segments is not None and stats["segment_count"] < min_segments:
        raise AssertionError(f"segment count below {min_segments}")
    if max_segments is not None and stats["segment_count"] > max_segments:
        raise AssertionError(f"segment count above {max_segments}")
    if min_speakers is not None and stats["speaker_count"] < min_speakers:
        raise AssertionError(f"speaker count below {min_speakers}")
    if max_speakers is not None and stats["speaker_count"] > max_speakers:
        raise AssertionError(f"speaker count above {max_speakers}")
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def validate_intermediate(
    asr_path: Path | None,
    diarization_path: Path | None,
    expected_language: str | None,
    exact_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> dict[str, Any]:
    """Validate versioned job intermediates without emitting recognized text."""
    stats: dict[str, Any] = {}
    if asr_path is not None:
        asr = _load_json(asr_path)
        if asr.get("schema_version") != 1 or not isinstance(asr.get("result"), dict):
            raise AssertionError("invalid ASR intermediate wrapper")
        result = asr["result"]
        language = str(result["language"])
        if expected_language is not None and language != expected_language:
            raise AssertionError(f"language was {language}, expected {expected_language}")
        stats["language"] = language
        stats["asr_segment_count"] = len(result["segments"])
    if diarization_path is not None:
        diarization = _load_json(diarization_path)
        if diarization.get("schema_version") != 1 or not isinstance(
            diarization.get("result"), dict
        ):
            raise AssertionError("invalid diarization intermediate wrapper")
        result = diarization["result"]
        speaker_count = int(result["num_speakers"])
        exclusive = result.get("exclusive_turns") or []
        previous_end = -1.0
        durations: list[float] = []
        speaker_durations: Counter[str] = Counter()
        for index, turn in enumerate(exclusive):
            start = float(turn["start"])
            end = float(turn["end"])
            if start < previous_end - 1e-6 or start >= end:
                raise AssertionError(f"overlapping or invalid exclusive turn at {index}")
            previous_end = end
            duration = end - start
            durations.append(duration)
            speaker_durations[str(turn["speaker"])] += duration
        if exact_speakers is not None and speaker_count != exact_speakers:
            raise AssertionError(f"speaker count was {speaker_count}, expected {exact_speakers}")
        if min_speakers is not None and speaker_count < min_speakers:
            raise AssertionError(f"speaker count below {min_speakers}")
        if max_speakers is not None and speaker_count > max_speakers:
            raise AssertionError(f"speaker count above {max_speakers}")
        mean_turn = sum(durations) / len(durations) if durations else 0.0
        total = sum(speaker_durations.values())
        dominant_ratio = max(speaker_durations.values(), default=0.0) / total if total else 0.0
        stats.update(
            {
                "speaker_count": speaker_count,
                "exclusive_turn_count": len(exclusive),
                "mean_exclusive_turn_seconds": round(mean_turn, 6),
                "dominant_speaker_ratio": round(dominant_ratio, 6),
            }
        )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def validate_artifacts(directory: Path, stem: str, extensions: list[str]) -> dict[str, Any]:
    files = {
        extension: _latest_artifact(directory, stem, extension).name for extension in extensions
    }
    stats = {"artifact_count": len(files), "files": files}
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def validate_collision(directory: Path, stem: str, extensions: list[str]) -> dict[str, Any]:
    missing = [
        name
        for extension in extensions
        for name in (f"{stem}.{extension}", f"{stem}_1.{extension}")
        if not (directory / name).is_file()
    ]
    if missing:
        raise AssertionError(f"collision outputs missing: {', '.join(missing)}")
    stats = {"base_and_numbered_files": len(extensions) * 2}
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def validate_equivalent(paths: list[Path]) -> dict[str, Any]:
    summaries: list[dict[str, float | int]] = []
    for path in paths:
        payload = _load_json(path)
        segments = payload["segments"]
        summaries.append(
            {
                "duration": float(payload["input"]["duration"]),
                "segments": len(segments),
                "characters": sum(len(str(segment.get("text", ""))) for segment in segments),
            }
        )
    durations = [float(item["duration"]) for item in summaries]
    segment_counts = [int(item["segments"]) for item in summaries]
    character_counts = [int(item["characters"]) for item in summaries]
    if max(durations) - min(durations) > 1.0:
        raise AssertionError("input duration differs by more than one second")
    if min(segment_counts, default=0) == 0 or max(segment_counts) / min(segment_counts) > 1.2:
        raise AssertionError("segment counts differ by more than 20 percent")
    if min(character_counts, default=0) == 0 or max(character_counts) / min(character_counts) > 1.1:
        raise AssertionError("recognized character counts differ by more than 10 percent")
    stats = {
        "input_count": len(paths),
        "durations": [round(value, 3) for value in durations],
        "segment_counts": segment_counts,
        "character_counts": character_counts,
    }
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def find_job_intermediate(jobs: Path, input_path: Path, stage: str) -> Path:
    """Find a stage artifact by manifest input path without printing transcript data."""
    target = input_path.resolve()
    matches: list[tuple[int, Path]] = []
    for manifest_path in jobs.glob("*/manifest.json"):
        try:
            manifest = _load_json(manifest_path)
            recorded = Path(str(manifest["input"]["path"])).resolve()
            if recorded == target:
                matches.append((manifest_path.stat().st_mtime_ns, manifest_path.parent))
        except (AssertionError, KeyError, OSError, TypeError, ValueError):
            continue
    if not matches:
        raise AssertionError(f"job not found for input file: {input_path.name}")
    job = max(matches)[1]
    artifact = job / f"{stage}.json"
    if not artifact.is_file():
        raise AssertionError(f"missing {stage} intermediate in job {job.name}")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    formats = subparsers.add_parser("formats")
    formats.add_argument("--dir", type=Path, required=True)
    formats.add_argument("--stem", required=True)
    formats.add_argument("--duration", type=float)
    formats.add_argument("--expect-speakers", action="store_true")
    formats.add_argument("--expect-bom", action="store_true")

    json_parser = subparsers.add_parser("json")
    json_parser.add_argument("path", type=Path)
    json_parser.add_argument("--duration", type=float)
    json_parser.add_argument("--min-segments", type=int)
    json_parser.add_argument("--max-segments", type=int)
    json_parser.add_argument("--min-speakers", type=int)
    json_parser.add_argument("--max-speakers", type=int)

    intermediate = subparsers.add_parser("intermediate")
    intermediate.add_argument("--asr", type=Path)
    intermediate.add_argument("--diarization", type=Path)
    intermediate.add_argument("--language")
    intermediate.add_argument("--exact-speakers", type=int)
    intermediate.add_argument("--min-speakers", type=int)
    intermediate.add_argument("--max-speakers", type=int)

    artifacts = subparsers.add_parser("artifacts")
    artifacts.add_argument("--dir", type=Path, required=True)
    artifacts.add_argument("--stem", required=True)
    artifacts.add_argument("--extensions", required=True)

    collision = subparsers.add_parser("collision")
    collision.add_argument("--dir", type=Path, required=True)
    collision.add_argument("--stem", required=True)
    collision.add_argument("--extensions", required=True)

    equivalent = subparsers.add_parser("equivalent")
    equivalent.add_argument("paths", type=Path, nargs="+")

    job = subparsers.add_parser("job")
    job.add_argument("--jobs", type=Path, required=True)
    job.add_argument("--input", type=Path, required=True)
    job.add_argument("--language")
    job.add_argument("--exact-speakers", type=int)
    job.add_argument("--min-speakers", type=int)
    job.add_argument("--max-speakers", type=int)

    args = parser.parse_args()
    if args.command == "formats":
        validate_formats(args.dir, args.stem, args.duration, args.expect_speakers, args.expect_bom)
    elif args.command == "json":
        validate_json(
            args.path,
            args.duration,
            args.min_segments,
            args.max_segments,
            args.min_speakers,
            args.max_speakers,
        )
    elif args.command == "intermediate":
        validate_intermediate(
            args.asr,
            args.diarization,
            args.language,
            args.exact_speakers,
            args.min_speakers,
            args.max_speakers,
        )
    elif args.command == "artifacts":
        validate_artifacts(args.dir, args.stem, args.extensions.split(","))
    elif args.command == "collision":
        validate_collision(args.dir, args.stem, args.extensions.split(","))
    elif args.command == "equivalent":
        validate_equivalent(args.paths)
    else:
        asr_path = find_job_intermediate(args.jobs, args.input, "asr")
        diarization_path = (
            find_job_intermediate(args.jobs, args.input, "diarization")
            if any(
                value is not None
                for value in (args.exact_speakers, args.min_speakers, args.max_speakers)
            )
            else None
        )
        validate_intermediate(
            asr_path,
            diarization_path,
            args.language,
            args.exact_speakers,
            args.min_speakers,
            args.max_speakers,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
