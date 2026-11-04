"""UI-2: the frontend reducer fixture is replayed against the authoritative B9 EventReducer.

``frontend/src/test/fixtures/b9-reducer-cases.json`` is also replayed by Vitest
(``frontend/src/transport/reducer.test.ts``). If the Python reducer changes, this test
fails until the shared fixture (and therefore the TypeScript port) is updated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from utteran_contracts.control import EventEnvelope
from utteran_contracts.events import EventReducer, SyncSnapshot

FIXTURES = Path(__file__).parents[1] / "frontend" / "src" / "test" / "fixtures"
FIXTURE = FIXTURES / "b9-reducer-cases.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _snapshot(step: dict[str, Any]) -> SyncSnapshot:
    return SyncSnapshot.model_validate(
        {
            "schema_version": 1,
            "snapshot_token": "0" * 32,
            "event_cursor": step["event_cursor"],
            "resources": step["resources"],
            "next_cursor": None,
            "queue": {
                "schema_version": 1,
                "revision": step["queue_revision"],
                "dispatch": "running",
                "active_item_id": None,
                "items": [],
            },
            "inventory_revision": None,
        }
    )


def _event(step: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "schema_version": 1,
            "id": step["id"],
            "resource_id": step["resource_id"],
            "resource_kind": step["resource_kind"],
            "resource_revision": step["resource_revision"],
            "timestamp": "2026-11-04T00:00:00Z",
            "type": step["type"],
            "payload": {"status": step["status"], "reason_code": None, "attempt_id": None},
        }
    )


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_matches_python_event_reducer(case: dict[str, Any]) -> None:
    reducer = EventReducer()
    for index, step in enumerate(case["steps"]):
        if step["op"] == "sync":
            reducer.apply_sync(_snapshot(step))
        elif step["op"] == "set_epoch":
            reducer.epoch = step["epoch"]
        elif step["op"] == "resource":
            outcome = reducer.apply_resource(step["id"], step["revision"], step["status"])
            assert outcome == step["expect"], (index, step)
        else:
            assert reducer.apply_event(_event(step)) == step["expect"], (index, step)
    final = case["final"]
    if "cursor" in final:
        assert str(reducer.cursor) == final["cursor"]
    for resource_id, status in final.get("statuses", {}).items():
        assert reducer.statuses[resource_id] == status
    if "queue_revision" in final:
        assert reducer.queue_revision == final["queue_revision"]
