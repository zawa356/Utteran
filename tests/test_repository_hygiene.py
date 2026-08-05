import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_private_runtime_paths_are_git_ignored() -> None:
    private_paths = (
        ".env",
        ".env.local",
        ".venvs/win-cpu/pyvenv.cfg",
        "input/private-recording.wav",
        "output/private-transcript.json",
        "jobs/private-job/manifest.json",
        "models/private-model/model.bin",
        "debug.log",
        "private-recording.mp4",
    )
    for path in private_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, f"sensitive runtime path is not ignored: {path}"


def test_placeholder_files_remain_trackable() -> None:
    for path in (".env.example", "input/.gitkeep", "output/.gitkeep"):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 1, f"public placeholder is unexpectedly ignored: {path}"
