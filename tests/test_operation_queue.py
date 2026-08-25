from __future__ import annotations

import time
from threading import Event

from utteran_gui.operation_queue import OperationQueue


def _wait_for(predicate: object, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_queue_runs_sequentially_and_continues_after_failure() -> None:
    queue = OperationQueue()
    release = Event()
    order: list[str] = []

    def first() -> str:
        order.append("first-start")
        release.wait(2.0)
        order.append("first-end")
        return "completed"

    def failed() -> str:
        order.append("failed")
        return "failed"

    def last() -> str:
        order.append("last")
        return "completed"

    queue.submit("1", kind="model_download", label="one", runner=first, canceller=lambda: None)
    queue.submit("2", kind="model_download", label="two", runner=failed, canceller=lambda: None)
    queue.submit("3", kind="transcription", label="three", runner=last, canceller=lambda: None)
    _wait_for(lambda: queue.snapshot("1")["status"] == "running")
    assert queue.snapshot("2")["status"] == "waiting"
    assert queue.snapshot("3")["position"] == 2

    release.set()
    _wait_for(lambda: queue.snapshot("3")["status"] == "completed")
    assert order == ["first-start", "first-end", "failed", "last"]
    assert queue.snapshot("2")["status"] == "failed"


def test_waiting_queue_item_can_be_cancelled_individually() -> None:
    queue = OperationQueue()
    release = Event()
    cancelled = Event()
    ran = Event()

    queue.submit(
        "active",
        kind="transcription",
        label="active",
        runner=lambda: (release.wait(2.0) and "completed") or "failed",
        canceller=lambda: release.set(),
    )
    queue.submit(
        "waiting",
        kind="model_download",
        label="waiting",
        runner=lambda: (ran.set() or "completed"),
        canceller=cancelled.set,
    )
    _wait_for(lambda: queue.snapshot("active")["status"] == "running")
    queue.cancel("waiting")
    release.set()
    _wait_for(lambda: queue.snapshot("active")["status"] == "completed")

    assert queue.snapshot("waiting")["status"] == "cancelled"
    assert cancelled.is_set()
    assert not ran.is_set()
