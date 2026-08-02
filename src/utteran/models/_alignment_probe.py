"""Disposable subprocess that exercises CTranslate2 word-timestamp alignment.

Some third-party CTranslate2 conversions of distilled Whisper models ship an
``alignment_heads`` config inherited unmodified from the original, deeper
decoder. Requesting word timestamps then makes CTranslate2 index a decoder
layer that does not exist, which crashes the process with no catchable
Python exception (a native access violation / segfault). Running the same
code path here, in a throwaway subprocess, lets the caller observe the crash
from the outside through the exit code instead of losing its own process.

Exit codes:
    0    alignment completed without a native crash.
    2    verification was inconclusive (an ordinary Python-level error, for
         example a missing dependency); the caller should not treat this as
         evidence of the crash.
    other / abnormal termination
         the native crash reproduced.
"""

from __future__ import annotations

import sys


def main(model_path: str) -> int:
    """Load the model and force one word-timestamp alignment pass."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        silence = np.zeros(16_000 * 2, dtype=np.float32)
        segments, _ = model.transcribe(
            silence,
            language="en",
            word_timestamps=True,
            vad_filter=False,
            no_speech_threshold=None,
            beam_size=1,
            condition_on_previous_text=False,
        )
        list(segments)
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
