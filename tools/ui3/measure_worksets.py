"""E-B: measure the physical size of the private worksets store for maximal legal states.

Synthetic values only (generated paths and terms, no real user data). Prints one JSON report;
docs/architecture/ui3/e-b-worksets.md records the numbers of the reviewed run.

    uv run --no-sync python tools/ui3/measure_worksets.py [--repeat 10000]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui.worksets_model import (  # noqa: E402
    MAX_GLOSSARY_TERMS,
    MAX_INPUTS,
    MAX_STATE_BYTES,
    MAX_TERM_CODEPOINTS,
    MAX_WORKSETS,
    canonical,
    empty_state,
)
from utteran_gui.worksets_store import WorksetError, WorksetStore, check_state  # noqa: E402

CJK = "語"  # 3 UTF-8 bytes
ASCII = "x"


def term(index: int, char: str) -> str:
    stem = f"{index:04d}"
    return stem + char * (MAX_TERM_CODEPOINTS - len(stem))


def glossary(seed: int, char: str) -> list[str]:
    return [term(seed * MAX_GLOSSARY_TERMS + i, char) for i in range(MAX_GLOSSARY_TERMS)]


def path(index: int, length: int, char: str, kind: str = "in") -> str:
    head = f"C:\\synthetic\\{kind}-{index:03d}-"
    return head + char * (length - len(head) - 4) + ".wav"


def state(
    rows: int, *, path_len: int, path_char: str, distinct: int, term_char: str
) -> dict[str, Any]:
    """``rows`` inputs with custom outputs and every override; ``distinct`` different glossaries."""
    value = empty_state()
    value["preset_ref"] = {"id": "pre_" + "0" * 32, "revision": 999}
    value["policy_ref"] = {"id": "pol_" + "0" * 32, "revision": 999}
    value["common"] = {
        "language": "ja",
        "formats": ["srt", "vtt", "json", "txt", "md"],
        "output_dir": path(0, path_len, path_char, "out"),
        "output_source": "picker",
    }
    value["glossaries"] = [glossary(g, term_char) for g in range(distinct)]
    value["inputs"] = [
        {
            "input_path": path(i, path_len, path_char),
            "source": "picker",
            "selected": True,
            "output": {"kind": "custom", "path": path(i, path_len, path_char, "out")},
            "overrides": {
                "asr_backend": "faster-whisper",
                "asr_model": "large-v3-turbo",
                "asr_device": "cpu",
                "language": "en",
                "formats": ["srt", "vtt", "json", "txt", "md"],
                **({"glossary": min(i, distinct - 1)} if distinct else {}),
            },
        }
        for i in range(rows)
    ]
    value["primary_index"] = 0 if rows else None
    return value


def size(value: dict[str, Any]) -> int:
    return len(canonical(value).encode("utf-8"))


def fits(value: dict[str, Any]) -> bool:
    try:
        check_state(value)
        return True
    except WorksetError as exc:
        assert exc.code == "state_too_large", exc.code
        return False


PEAK_JOURNAL = {"bytes": 0}


class Observed(sqlite3.Connection):
    """Records the rollback journal size right before each commit (its largest moment)."""

    path: Path | None = None

    def commit(self) -> None:
        if Observed.path is not None:
            journal = Observed.path.with_name(Observed.path.name + "-journal")
            if journal.exists():
                PEAK_JOURNAL["bytes"] = max(PEAK_JOURNAL["bytes"], journal.stat().st_size)
        super().commit()


def journal_bytes(store: WorksetStore) -> int:
    journal = store.path.with_name(store.path.name + "-journal")
    return journal.stat().st_size if journal.exists() else 0


def observe(store: WorksetStore) -> WorksetStore:
    Observed.path = store.path
    PEAK_JOURNAL["bytes"] = 0
    store.factory = Observed
    return store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=10000)
    args = parser.parse_args()
    configs = {
        "typical_max_shared_cjk_glossary": state(
            MAX_INPUTS, path_len=260, path_char=CJK, distinct=1, term_char=CJK
        ),
        "max_rows_no_glossary_ascii_32k_paths": state(
            MAX_INPUTS, path_len=32768, path_char=ASCII, distinct=0, term_char=ASCII
        ),
        "max_rows_cjk_32k_paths": state(
            MAX_INPUTS, path_len=32768, path_char=CJK, distinct=0, term_char=CJK
        ),
        "distinct_cjk_glossaries_10": state(
            10, path_len=260, path_char=CJK, distinct=10, term_char=CJK
        ),
        "distinct_cjk_glossaries_11": state(
            11, path_len=260, path_char=CJK, distinct=11, term_char=CJK
        ),
        "distinct_ascii_glossaries_32": state(
            32, path_len=260, path_char=ASCII, distinct=32, term_char=ASCII
        ),
        "distinct_cjk_glossaries_100_unbounded": state(
            MAX_INPUTS, path_len=260, path_char=CJK, distinct=100, term_char=CJK
        ),
    }
    report: dict[str, Any] = {
        "limits": {"max_state_bytes": MAX_STATE_BYTES, "max_worksets": MAX_WORKSETS},
        "states": {},
    }
    for name, value in configs.items():
        report["states"][name] = {"bytes": size(value), "accepted": fits(value)}

    with tempfile.TemporaryDirectory() as directory:
        clock = [1_800_000_000.0]
        store = observe(WorksetStore(Path(directory) / "w.sqlite3", clock=lambda: clock[0]))
        store.bootstrap("set 1")
        typical = configs["typical_max_shared_cjk_glossary"]
        big = configs["distinct_cjk_glossaries_10"]
        ids = [store.list()["items"][0]["id"]]
        for index in range(1, MAX_WORKSETS):
            ids.append(store.create(f"synthetic-key-{index:04d}", f"set {index + 1}", None)[1].id)
        revisions = {i: 1 for i in ids}

        def put(workset: str, value: dict[str, Any]) -> None:
            revisions[workset] = store.update(workset, revisions[workset], "set", value).revision

        for workset in ids:
            put(workset, typical)
        report["store_20_typical_max"] = {
            **store.physical(),
            "journal_after_commit": journal_bytes(store),
            "peak_journal_before_commit": PEAK_JOURNAL["bytes"],
        }
        PEAK_JOURNAL["bytes"] = 0
        # Fill to the store quota with the largest accepted states, with recovery checkpoints.
        refused = 0
        for round_ in range(3):
            clock[0] += 3600
            for workset in ids:
                variant = json.loads(
                    json.dumps(big if round_ == 0 or revisions[workset] > 2 else typical)
                )
                variant["common"]["language"] = ["ja", "en", "auto"][round_]
                try:
                    put(workset, variant)
                except WorksetError as exc:
                    assert exc.code == "store_quota_exceeded", exc.code
                    refused += 1
                    variant = json.loads(json.dumps(typical))
                    variant["common"]["language"] = ["ja", "en", "auto"][round_]
                    put(workset, variant)
        listing = store.list()
        report["store_quota_filled_with_recovery"] = {
            **store.physical(),
            "current_state_bytes": sum(i["state_bytes"] for i in listing["items"]),
            "refused_store_quota": refused,
            "journal_after_commit": journal_bytes(store),
            "peak_journal_before_commit": PEAK_JOURNAL["bytes"],
        }

        # Repeated upserts of one workset (autosave): the file must not grow with the count.
        small = Path(directory) / "repeat.sqlite3"
        repeat = WorksetStore(small, clock=lambda: clock[0])
        repeat.bootstrap("set 1")
        target = repeat.list()["items"][0]["id"]
        revision = 1
        samples = {}
        base = configs["typical_max_shared_cjk_glossary"]
        for index in range(1, args.repeat + 1):
            value = json.loads(json.dumps(base)) if index == 1 else value  # type: ignore[has-type]
            value["inputs"][index % MAX_INPUTS]["selected"] = index % 2 == 0
            clock[0] += 1
            revision = repeat.update(target, revision, "set 1", value).revision
            if index in (1, 10, 100, 1000, 5000, 10000) and index <= args.repeat:
                samples[index] = repeat.physical()
        report["repeat_upsert_typical_max"] = samples
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
