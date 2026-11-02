"""Test-only synthetic inference injection into the real worker and pipeline."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
import utteran.pipeline
from test_application import SyntheticASR, normalize
from utteran import application, worker_runtime
from utteran.worker import main

mode = sys.argv[1]


class Backend(SyntheticASR):
    def transcribe(self, audio_path, options, progress=None, cancel=None):
        os.write(1, b"PRIVATE_PROMPT backend stdout /private/input\n")
        os.write(2, b"PRIVATE_PROMPT backend stderr hf_synthetic123\n")
        if mode == "error":
            raise RuntimeError("PRIVATE_PROMPT backend error")
        if mode == "cancel":
            while not cancel.is_cancelled:
                time.sleep(0.01)
            cancel.raise_if_cancelled()
        return super().transcribe(audio_path, options, progress, cancel)


original = application.execute_single
backend = Backend()


def execute(*args, **kwargs):
    return original(*args, **kwargs, asr_backend=backend)


utteran.pipeline.normalize_audio = normalize
application.execute_single = execute
worker_runtime.offline_preflight = lambda cfg: None
sys.argv = ["utteran.worker", "--protocol", "1"]
os._exit(main())
