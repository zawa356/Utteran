"""Pure event-order and identity validation; not a GUI job state machine."""

from __future__ import annotations

from utteran_contracts.worker import (
    Event,
    WorkerAccepted,
    WorkerCancelAcknowledged,
    WorkerCompleted,
    WorkerError,
    WorkerErrorCode,
    WorkerHello,
    WorkerRequest,
)
from utteran_contracts.worker_framing import ProtocolError


class EventSequence:
    def __init__(self, request: WorkerRequest) -> None:
        self.request = request
        self.sequence = 0
        self.accepted = False
        self.terminal: WorkerCompleted | WorkerError | None = None
        self.cancel_sent = False
        self.cancel_acknowledged = False

    def accept(self, event: Event) -> None:
        if event.sequence != self.sequence or self.terminal is not None:
            raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
        if self.sequence == 0:
            if not isinstance(event, WorkerHello):
                raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
            if event.profile != self.request.profile:
                raise ProtocolError(WorkerErrorCode.ID_MISMATCH)
            if len(event.capabilities) != 2 or set(event.capabilities) != {"single", "cancel"}:
                raise ProtocolError(WorkerErrorCode.PROTOCOL_MISMATCH)
        else:
            if isinstance(event, WorkerHello):
                raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
            if any(
                getattr(event, key) != getattr(self.request, key)
                for key in ("request_id", "job_id", "attempt_id")
            ):
                raise ProtocolError(WorkerErrorCode.ID_MISMATCH)
            if isinstance(event, WorkerAccepted):
                if self.accepted:
                    raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
                self.accepted = True
            elif isinstance(event, WorkerError):
                self.terminal = event
            elif not self.accepted:
                raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
            elif isinstance(event, WorkerCancelAcknowledged):
                if not self.cancel_sent:
                    raise ProtocolError(WorkerErrorCode.INVALID_SEQUENCE)
                self.cancel_acknowledged = True
            elif isinstance(event, WorkerCompleted):
                self.terminal = event
        self.sequence += 1
