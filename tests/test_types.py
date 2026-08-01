from __future__ import annotations

import json

import pytest

from utteran.errors import CancelledError
from utteran.types import (
    CancelToken,
    DiarizationResult,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)


def test_transcription_result_round_trip_is_json_serializable() -> None:
    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, " hello", [Word(0.0, 1.0, " hello", 0.9)])],
        language="en",
        duration=1.0,
        backend="fake",
        model_id="fake-model",
        device="cpu",
    )

    restored = TranscriptionResult.from_dict(json.loads(json.dumps(result.to_dict())))

    assert restored == result


def test_diarization_result_round_trip_is_json_serializable() -> None:
    result = DiarizationResult(
        turns=[SpeakerTurn(0.0, 1.0, "SPEAKER_00")],
        exclusive_turns=[SpeakerTurn(0.0, 1.0, "SPEAKER_00")],
        num_speakers=1,
        backend="fake",
        model_id="fake-model",
        device="cpu",
    )

    restored = DiarizationResult.from_dict(json.loads(json.dumps(result.to_dict())))

    assert restored == result


def test_cancel_token_raises_after_cancellation() -> None:
    token = CancelToken()
    token.cancel()

    with pytest.raises(CancelledError):
        token.raise_if_cancelled()
