"""In-memory serial queue shared by transcription and model operations."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

QueueStatus = Literal["waiting", "running", "completed", "failed", "cancelled"]
TERMINAL_QUEUE_STATUSES = frozenset({"completed", "failed", "cancelled"})
Runner = Callable[[], QueueStatus]
Canceller = Callable[[], None]


@dataclass
class QueueItem:
    id: str
    kind: str
    label: str
    runner: Runner = field(repr=False)
    canceller: Canceller = field(repr=False)
    status: QueueStatus = "waiting"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None


class OperationQueue:
    """Execute all resource-heavy GUI operations one at a time.

    State deliberately lives only for the GUI process lifetime. A killed process
    cannot safely resume a partially downloaded model or transcription command;
    both underlying CLIs already provide explicit retry/resume behavior.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._items: dict[str, QueueItem] = {}
        self._order: list[str] = []
        self._worker = threading.Thread(
            target=self._work, name="utteran-operation-queue", daemon=True
        )
        self._worker.start()

    def submit(
        self, item_id: str, *, kind: str, label: str, runner: Runner, canceller: Canceller
    ) -> dict[str, object]:
        with self._condition:
            if item_id in self._items:
                raise ValueError(f"Queue item already exists: {item_id}")
            self._items[item_id] = QueueItem(item_id, kind, label, runner, canceller)
            self._order.append(item_id)
            self._condition.notify()
            return self._snapshot(self._items[item_id])

    def cancel(self, item_id: str) -> dict[str, object]:
        with self._condition:
            item = self._item(item_id)
            if item.status in TERMINAL_QUEUE_STATUSES:
                return self._snapshot(item)
            was_waiting = item.status == "waiting"
            if was_waiting:
                item.status = "cancelled"
                item.finished_at = datetime.now(UTC).isoformat()
            canceller = item.canceller
            self._condition.notify_all()
        canceller()
        return self.snapshot(item_id)

    def snapshot(self, item_id: str) -> dict[str, object]:
        with self._condition:
            return self._snapshot(self._item(item_id))

    def list(self) -> list[dict[str, object]]:
        with self._condition:
            return [self._snapshot(self._items[item_id]) for item_id in self._order]

    def _work(self) -> None:
        while True:
            with self._condition:
                item = self._next_waiting()
                while item is None:
                    self._condition.wait()
                    item = self._next_waiting()
                item.status = "running"
                item.started_at = datetime.now(UTC).isoformat()
            try:
                result = item.runner()
                terminal = result if result in TERMINAL_QUEUE_STATUSES else "completed"
            except Exception:
                terminal = "failed"
            with self._condition:
                item.status = terminal
                item.finished_at = datetime.now(UTC).isoformat()
                self._condition.notify_all()

    def _next_waiting(self) -> QueueItem | None:
        if any(item.status == "running" for item in self._items.values()):
            return None
        return next(
            (
                self._items[item_id]
                for item_id in self._order
                if self._items[item_id].status == "waiting"
            ),
            None,
        )

    def _snapshot(self, item: QueueItem) -> dict[str, object]:
        waiting = [item_id for item_id in self._order if self._items[item_id].status == "waiting"]
        return {
            "id": item.id,
            "kind": item.kind,
            "label": item.label,
            "status": item.status,
            "position": waiting.index(item.id) + 1 if item.id in waiting else None,
            "created_at": item.created_at,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
        }

    def _item(self, item_id: str) -> QueueItem:
        try:
            return self._items[item_id]
        except KeyError:
            raise KeyError(item_id) from None
