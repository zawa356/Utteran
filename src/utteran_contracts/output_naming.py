"""Pure naming of the per-job output directory and its artifacts (0.1.54).

``<input stem>_<YYYYMMDD-HHMMSS>_<short job id>``: the directory and every artifact in it
share this one stem, so results of different jobs never share a file name and a job never
writes a generic ``result.srt``. The timestamp is the submission time frozen in the job
snapshot; the name is a function of frozen values only (same on every attempt).

No filesystem access here. Windows rules are applied on every platform so a data root can
move between machines.
"""

from __future__ import annotations

import re
import unicodedata

STAMP_PATTERN = r"^[0-9]{8}-[0-9]{6}$"
STEM_LIMIT = 80
STEM_MINIMUM = 8
# Conservative budget for "<parent>/<name>/<name>.<ext>" below Windows MAX_PATH (260).
PATH_BUDGET = 240
_LONGEST_SUFFIX = len(".json")
# Hex digits of the job id used in the name. The first is the normal case; the longer
# ones are used only when a shorter name is already taken by something else (two job ids
# sharing a prefix, or a directory the user created). The last is the full id.
ID_LENGTHS = (8, 12, 16, 24, 32)
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$")
_STAMP = re.compile(STAMP_PATTERN)


def input_stem(input_path: str) -> str:
    """File name without its last suffix, for POSIX and Windows separators alike."""
    name = re.split(r"[\\/]", input_path)[-1]
    head, dot, _ = name.rpartition(".")
    return head if dot and head else name


def safe_stem(stem: str, limit: int = STEM_LIMIT) -> str:
    """A Windows-safe single path component; never empty, never ends with dot/space."""
    text = unicodedata.normalize("NFC", stem)
    text = _FORBIDDEN.sub("_", text).strip(" .")
    text = text[: max(limit, 1)].rstrip(" .")
    return text or "input"


def job_output_name(
    input_path: str, stamp: str, job_id: str, *, parent: str, id_length: int = ID_LENGTHS[0]
) -> str:
    """Directory name (= artifact stem) for one job, or ValueError with a fixed code.

    The job id part separates equal stems, equal seconds and case-insensitive
    filesystems; a reserved device name (CON, NUL, ...) cannot survive because the
    suffix is always appended. A shortened id is not unique by itself: the caller must
    claim the directory and move to the next ``id_length`` when it is taken.
    """
    if not _STAMP.fullmatch(stamp) or not _JOB_ID.fullmatch(job_id) or id_length not in ID_LENGTHS:
        raise ValueError("output_name_invalid")
    suffix = f"_{stamp}_{job_id[4 : 4 + id_length]}"
    # Both occurrences of the name count against the path budget.
    room = (PATH_BUDGET - len(parent) - 2 - _LONGEST_SUFFIX) // 2 - len(suffix)
    if room < STEM_MINIMUM:
        raise ValueError("output_path_too_long")
    return safe_stem(input_stem(input_path), min(STEM_LIMIT, room)) + suffix


_REEXPORT_ID = re.compile(r"^rex_[0-9a-f]{32}$")


def reexport_output_name(
    input_path: str, stamp: str, reexport_id: str, *, parent: str, id_length: int = ID_LENGTHS[0]
) -> str:
    """E-C: ``<input stem>_reexport_<YYYYMMDD-HHMMSS>_<short re-export id>``.

    Same rules as :func:`job_output_name` (Windows-safe stem, path budget for both
    occurrences of the name, longer id forms only when a shorter name is taken). The
    ``_reexport_`` part keeps it apart from every job directory name of the same input.
    """
    if (
        not _STAMP.fullmatch(stamp)
        or not _REEXPORT_ID.fullmatch(reexport_id)
        or id_length not in ID_LENGTHS
    ):
        raise ValueError("output_name_invalid")
    suffix = f"_reexport_{stamp}_{reexport_id[4 : 4 + id_length]}"
    room = (PATH_BUDGET - len(parent) - 2 - _LONGEST_SUFFIX) // 2 - len(suffix)
    if room < STEM_MINIMUM:
        raise ValueError("output_path_too_long")
    return safe_stem(input_stem(input_path), min(STEM_LIMIT, room)) + suffix
