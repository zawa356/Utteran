"""E-B (Issue #80): private named worksets store and ``/api/v1/worksets`` (``--next-ui-v1``)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.worksets_api import input_status
from utteran_gui.worksets_model import (
    CHECKPOINT_INTERVAL_SECONDS,
    MAX_GLOSSARY_TERMS,
    MAX_INPUTS,
    MAX_STATE_BYTES,
    MAX_WORKSETS,
    RECOVERY_GENERATIONS,
    canonical,
    empty_state,
    state_schema,
)
from utteran_gui.worksets_store import WorksetError, WorksetStore
from utteran_paths import resolve_data_paths

ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"
SESSION = "synthetic-session"
SCHEMA = ROOT / "docs/architecture/ui3/e-b-worksets.schema.json"
# Assembled at runtime: a value that must never appear in a log, error or public answer.
SENTINEL = "private" + "-sentinel-" + "7f3a"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> WorksetStore:
    created = WorksetStore(tmp_path / "private" / "worksets.sqlite3", clock=clock)
    created.bootstrap("作業セット1")
    return created


def first(store: WorksetStore) -> str:
    return str(store.list()["items"][0]["id"])


def row(path: str, **overrides: object) -> dict[str, object]:
    return {
        "input_path": path,
        "source": "typed",
        "selected": True,
        "output": {"kind": "inherit"},
        "overrides": overrides,
    }


def state(*rows: dict[str, object], glossaries: list[list[str]] | None = None) -> dict[str, object]:
    value = empty_state()
    value["inputs"] = list(rows)
    value["primary_index"] = 0 if rows else None
    value["glossaries"] = glossaries or []
    return value


# ---- data paths and opt-in -----------------------------------------------------------------


def test_store_location_is_resolved_and_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(tmp_path / "portable"))
    paths = resolve_data_paths(ROOT)
    assert paths.worksets_store == tmp_path / "portable/data/worksets-v1/worksets.sqlite3"
    assert paths.worksets_store.parent not in {
        paths.settings_resources.parent,
        paths.control_store.parent,
    }
    monkeypatch.delenv("UTTERAN_DATA_ROOT")
    default = resolve_data_paths(tmp_path).worksets_store
    assert default.name == "worksets.sqlite3" and not default.is_relative_to(ROOT)


def test_default_gui_has_no_worksets_routes_or_store(tmp_path):
    app = create_app(SESSION, repo_root=ROOT)
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        assert c.get("/api/v1/worksets").status_code == 404
    code = (
        "import sys; from pathlib import Path; from utteran_gui.api import create_app;"
        f"create_app('k', repo_root=Path({str(ROOT)!r}));"
        "print(any(m.startswith('utteran_gui.worksets') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        SESSION,
        repo_root=ROOT,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=tmp_path / "b5" / "resources.sqlite3",
        v1_worksets_path=tmp_path / "ws" / "worksets.sqlite3",
    )
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        yield c


def boot(client: TestClient, name: str = "作業セット1") -> dict:
    response = client.post("/api/v1/worksets/bootstrap", json={"schema_version": 1, "name": name})
    assert response.status_code == 200, response.text
    return response.json()


def test_reads_never_create_the_store(client, tmp_path):
    listing = client.get("/api/v1/worksets").json()
    assert listing["exists"] is False and listing["items"] == [] and listing["active"] is None
    assert client.get("/api/v1/worksets/ws_" + "0" * 32).status_code == 404
    assert not (tmp_path / "ws").exists()
    assert listing["limits"] == {
        "max_worksets": 20,
        "max_inputs": 100,
        "max_state_bytes": 8 * 1024 * 1024,
        "max_store_state_bytes": 64 * 1024 * 1024,
        "recovery_generations": 2,
    }


@pytest.mark.parametrize(
    "headers,status",
    [
        ({SESSION_HEADER: "wrong"}, 401),
        ({SESSION_HEADER: SESSION, "Host": "evil.example"}, 403),
        ({SESSION_HEADER: SESSION, "Origin": "http://evil.example"}, 403),
    ],
)
def test_session_host_and_origin_are_enforced(client, headers, status):
    assert client.get("/api/v1/worksets", headers=headers).status_code == status


def test_mutation_without_origin_or_header_is_refused(tmp_path):
    app = create_app(
        SESSION,
        repo_root=ROOT,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=tmp_path / "b5.sqlite3",
        v1_worksets_path=tmp_path / "ws" / "w.sqlite3",
    )
    with TestClient(
        app, base_url="http://" + AUTHORITY, cookies={"utteran_gui_session": SESSION}
    ) as c:
        answer = c.post("/api/v1/worksets/bootstrap", json={"schema_version": 1, "name": "x"})
    assert answer.status_code == 403 and answer.json()["error"]["code"] == "origin_required"
    assert not (tmp_path / "ws").exists()


# ---- first run -----------------------------------------------------------------------------


def test_bootstrap_creates_exactly_one_default_workset_under_concurrency(tmp_path):
    path = tmp_path / "ws" / "worksets.sqlite3"
    barrier = threading.Barrier(8)

    def open_and_boot(index: int) -> dict:
        barrier.wait()
        return WorksetStore(path, timeout=5).bootstrap(f"作業セット{index}")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(open_and_boot, range(8)))
    ids = {r["items"][0]["id"] for r in results}
    assert len(ids) == 1 and all(len(r["items"]) == 1 for r in results)
    assert results[0]["active"]["workset_id"] in ids


def test_bootstrap_across_processes_creates_one(tmp_path):
    path = tmp_path / "ws" / "worksets.sqlite3"
    script = (
        "import sys,json; from pathlib import Path;"
        "from utteran_gui.worksets_store import WorksetStore;"
        "store = WorksetStore(Path(sys.argv[1]), timeout=10);"
        "print(json.dumps(store.bootstrap('作業セット1')['items']))"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True
        )
        for _ in range(4)
    ]
    outputs = [json.loads(child.communicate(timeout=60)[0]) for child in children]
    assert all(child.returncode == 0 for child in children)
    assert len({o[0]["id"] for o in outputs}) == 1 and all(len(o) == 1 for o in outputs)


def test_bootstrap_is_idempotent_and_keeps_edits(client):
    listing = boot(client)
    workset = listing["items"][0]
    assert workset["name"] == "作業セット1" and listing["active"]["view"] == {"mode": "normal"}
    edited = state(row("/synthetic/a.wav"))
    put = client.put(
        f"/api/v1/worksets/{workset['id']}",
        json={"schema_version": 1, "name": "作業セット1", "state": edited},
        headers={"If-Match": '"1"'},
    )
    assert put.status_code == 200 and put.headers["ETag"] == '"2"'
    again = boot(client, "Workset 1")
    assert [i["id"] for i in again["items"]] == [workset["id"]]
    assert (
        client.get(f"/api/v1/worksets/{workset['id']}").json()["state"]["inputs"][0]["input_path"]
        == "/synthetic/a.wav"
    )


# ---- CRUD and CAS --------------------------------------------------------------------------


def test_put_requires_strong_if_match_and_detects_conflicts(client):
    workset = boot(client)["items"][0]["id"]
    url = f"/api/v1/worksets/{workset}"
    body = {"schema_version": 1, "name": "A", "state": state(row("/s/a.wav"))}
    assert client.put(url, json=body).status_code == 428
    assert client.put(url, json=body, headers={"If-Match": 'W/"1"'}).status_code == 400
    assert client.put(url, json=body, headers={"If-Match": '"1"'}).status_code == 200
    # Another window still holds revision 1: refused, the newer content stays.
    other = {"schema_version": 1, "name": "B", "state": state(row("/s/b.wav"))}
    conflict = client.put(url, json=other, headers={"If-Match": '"1"'})
    assert conflict.status_code == 412
    assert conflict.json()["error"] == {
        "code": "revision_conflict",
        "message_key": "worksets.revision_conflict",
        "request_id": conflict.json()["error"]["request_id"],
        "retryable": False,
        "issues": [],
    }
    current = client.get(url).json()
    assert current["name"] == "A" and current["revision"] == 2


def test_lost_answer_is_reconciled_by_get_not_by_blind_resend(client):
    workset = boot(client)["items"][0]["id"]
    url = f"/api/v1/worksets/{workset}"
    body = {"schema_version": 1, "name": "作業セット1", "state": state(row("/s/a.wav"))}
    assert client.put(url, json=body, headers={"If-Match": '"1"'}).status_code == 200
    # The client never saw the answer. A resend under the same If-Match cannot write twice...
    assert client.put(url, json=body, headers={"If-Match": '"1"'}).status_code == 412
    # ...and GET shows that exactly the sent content is stored (canonical equality).
    stored = client.get(url).json()
    assert stored["revision"] == 2 and canonical(stored["state"]) == canonical(body["state"])


def test_identical_put_creates_no_revision(client):
    workset = boot(client)["items"][0]["id"]
    url = f"/api/v1/worksets/{workset}"
    current = client.get(url).json()
    same = client.put(
        url,
        json={"schema_version": 1, "name": current["name"], "state": current["state"]},
        headers={"If-Match": '"1"'},
    )
    assert same.status_code == 200 and same.json()["revision"] == 1


def test_create_is_idempotent_and_limited(client):
    boot(client)
    body = {"schema_version": 1, "name": "案件A", "state": state(row("/s/a.wav"))}
    key = {"Idempotency-Key": "synthetic-create-0001"}
    created = client.post("/api/v1/worksets", json=body, headers=key)
    assert created.status_code == 201 and created.headers["Location"].endswith(created.json()["id"])
    replay = client.post("/api/v1/worksets", json=body, headers=key)
    assert replay.status_code == 201 and replay.json()["id"] == created.json()["id"]
    changed = client.post("/api/v1/worksets", json={**body, "name": "案件B"}, headers=key)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    assert client.post("/api/v1/worksets", json=body).status_code == 400
    for index in range(2, MAX_WORKSETS):
        made = client.post(
            "/api/v1/worksets",
            json={"schema_version": 1, "name": f"set {index}"},
            headers={"Idempotency-Key": f"synthetic-create-{index:04d}"},
        )
        assert made.status_code == 201, made.text
    before = client.get("/api/v1/worksets").json()
    assert len(before["items"]) == MAX_WORKSETS
    refused = client.post(
        "/api/v1/worksets",
        json={"schema_version": 1, "name": "one too many"},
        headers={"Idempotency-Key": "synthetic-create-9999"},
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "workset_limit"
    assert client.get("/api/v1/worksets").json() == before


def test_create_before_bootstrap_is_refused(client, tmp_path):
    answer = client.post(
        "/api/v1/worksets",
        json={"schema_version": 1, "name": "x"},
        headers={"Idempotency-Key": "synthetic-create-0001"},
    )
    assert answer.status_code == 409 and answer.json()["error"]["code"] == "store_not_initialized"
    assert not (tmp_path / "ws").exists()


def test_delete_needs_cas_never_removes_the_last_and_moves_active(client, tmp_path):
    first_id = boot(client)["items"][0]["id"]
    second = client.post(
        "/api/v1/worksets",
        json={"schema_version": 1, "name": "二つ目"},
        headers={"Idempotency-Key": "synthetic-create-0002"},
    ).json()["id"]
    active = client.put(
        "/api/v1/worksets/active",
        json={"schema_version": 1, "workset_id": second, "view": {"mode": "expert"}},
        headers={"If-Match": '"1"'},
    ).json()
    assert active["workset_id"] == second and active["view"] == {"mode": "expert"}
    b5 = tmp_path / "b5"
    before = sorted(p.name for p in b5.rglob("*")) if b5.exists() else []
    assert client.delete(f"/api/v1/worksets/{second}").status_code == 428
    assert (
        client.delete(f"/api/v1/worksets/{second}", headers={"If-Match": '"9"'}).status_code == 412
    )
    deleted = client.delete(f"/api/v1/worksets/{second}", headers={"If-Match": '"1"'})
    assert deleted.status_code == 200
    assert [i["id"] for i in deleted.json()["items"]] == [first_id]
    assert deleted.json()["active"]["workset_id"] == first_id
    assert deleted.json()["active"]["view"] == {"mode": "expert"}
    last = client.delete(f"/api/v1/worksets/{first_id}", headers={"If-Match": '"1"'})
    assert last.status_code == 409 and last.json()["error"]["code"] == "workset_last"
    assert (sorted(p.name for p in b5.rglob("*")) if b5.exists() else []) == before


def test_active_pointer_is_cas_and_validated(client):
    workset = boot(client)["items"][0]["id"]
    second = client.post(
        "/api/v1/worksets",
        json={"schema_version": 1, "name": "二つ目"},
        headers={"Idempotency-Key": "synthetic-create-0002"},
    ).json()["id"]
    url = "/api/v1/worksets/active"
    body = {"schema_version": 1, "workset_id": workset, "view": {"mode": "expert"}}
    assert client.put(url, json=body).status_code == 428
    assert client.put(url, json=body, headers={"If-Match": "1"}).status_code == 400
    one = client.put(url, json=body, headers={"If-Match": '"1"'})
    assert one.status_code == 200 and one.headers["ETag"] == '"2"'
    # The same value again changes nothing, whatever revision the sender knew.
    two = client.put(url, json=body, headers={"If-Match": '"1"'}).json()
    assert two == one.json() and two["revision"] == 2
    assert client.get(url).json() == two and client.get(url).headers["ETag"] == '"2"'
    # A window chose W2 (rev 2 -> 3); a delayed older request (sent with rev 1) arrives late.
    newer = {**body, "workset_id": second}
    assert client.put(url, json=newer, headers={"If-Match": '"2"'}).status_code == 200
    stale = client.put(url, json={**body, "view": {"mode": "normal"}}, headers={"If-Match": '"2"'})
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "revision_conflict"
    assert client.get(url).json()["workset_id"] == second  # never moved back
    bad = client.put(url, json={**body, "view": {"mode": "debug"}}, headers={"If-Match": '"3"'})
    assert bad.status_code == 422
    missing = client.put(
        url, json={**body, "workset_id": "ws_" + "1" * 32}, headers={"If-Match": '"3"'}
    )
    assert missing.status_code == 404


# ---- the saved projection ------------------------------------------------------------------


def test_round_trip_keeps_inherit_explicit_empty_and_revisions(client):
    workset = boot(client)["items"][0]["id"]
    value = state(
        row("/s/a.wav", glossary=0, formats=["srt"], language="en"),
        row("/s/b.wav"),
        row("/s/c.wav", glossary=1),
        row("/s/d.wav", glossary=0),
        glossaries=[[], ["ACME", "用語"]],
    )
    value["inputs"][1]["selected"] = False
    value["inputs"][1]["output"] = {"kind": "custom", "path": "/s/out"}
    value["primary_index"] = 2
    value["preset_ref"] = {"id": "pre_" + "a" * 32, "revision": 3}
    value["policy_ref"] = {"id": "pol_" + "b" * 32, "revision": 1}
    value["common"] = {
        "language": None,
        "formats": [],
        "output_dir": "/s/o",
        "output_source": "picker",
    }
    put = client.put(
        f"/api/v1/worksets/{workset}",
        json={"schema_version": 1, "name": "x", "state": value},
        headers={"If-Match": '"1"'},
    )
    assert put.status_code == 200, put.text
    stored = client.get(f"/api/v1/worksets/{workset}").json()["state"]
    assert stored == json.loads(canonical(value))
    assert "glossary" not in stored["inputs"][1]["overrides"]
    assert stored["glossaries"][0] == [] and stored["common"]["formats"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["inputs"][0]["overrides"].update(language=None),
        lambda s: s["inputs"][0].update(job_id="j"),
        lambda s: s.update(resolution_token="t"),
        lambda s: s.update(primary_index=5),
        lambda s: s.update(primary_index=None),
        lambda s: s.update(schema_version=2),
        lambda s: s["inputs"][0]["overrides"].update(glossary=1),
        lambda s: s.update(glossaries=[["a"], ["a"]]),
        lambda s: s["inputs"][0]["output"].update(kind="custom"),
        lambda s: s["inputs"][0].update(input_path=""),
        lambda s: s["inputs"][0]["overrides"].update(formats=["srt", "srt"]),
        lambda s: s["inputs"].extend([deepcopy(s["inputs"][0])] * MAX_INPUTS),
        lambda s: s.update(glossaries=[["x" * 257]]),
        lambda s: s.update(glossaries=[["t"] * (MAX_GLOSSARY_TERMS + 1)]),
        lambda s: s.update(primary_index=True),
    ],
)
def test_invalid_states_are_refused_and_the_old_state_kept(client, mutate):
    workset = boot(client)["items"][0]["id"]
    value = state(row("/s/a.wav", glossary=0), glossaries=[["a"]])
    mutate(value)
    answer = client.put(
        f"/api/v1/worksets/{workset}",
        json={"schema_version": 1, "name": "x", "state": value},
        headers={"If-Match": '"1"'},
    )
    assert answer.status_code == 422 and answer.json()["error"]["code"] in {
        "state_invalid",
        "schema_version_unsupported",
    }
    assert client.get(f"/api/v1/worksets/{workset}").json()["revision"] == 1


def test_names_are_checked(client):
    workset = boot(client)["items"][0]["id"]
    for name in ["", "   ", "a\nb", "x" * 121, 5]:
        answer = client.put(
            f"/api/v1/worksets/{workset}",
            json={"schema_version": 1, "name": name, "state": empty_state()},
            headers={"If-Match": '"1"'},
        )
        assert answer.status_code == 422 and answer.json()["error"]["code"] == "name_invalid"


def test_too_large_state_is_refused_explicitly_and_kept(store):
    workset = first(store)
    small = state(row("/s/a.wav"))
    revision = store.update(workset, 1, "x", small).revision
    huge = state(*[row("C:/" + "語" * 30000 + f"{i}.wav") for i in range(MAX_INPUTS)])
    assert len(canonical(huge).encode()) > MAX_STATE_BYTES
    with pytest.raises(WorksetError) as error:
        store.update(workset, revision, "x", huge)
    assert error.value.code == "state_too_large" and error.value.status == 413
    assert json.loads(store.get(workset).state) == json.loads(canonical(small))


def test_store_quota_refuses_without_truncating(store, monkeypatch):
    import utteran_gui.worksets_store as module

    monkeypatch.setattr(module, "MAX_STORE_STATE_BYTES", 4096)
    workset = first(store)
    with pytest.raises(WorksetError, match="store_quota_exceeded"):
        store.update(workset, 1, "x", state(row("/s/" + "a" * 5000)))
    assert store.get(workset).revision == 1


# ---- recovery generations ------------------------------------------------------------------


def test_recovery_keeps_at_most_two_checkpoints_and_restores_them(store, clock):
    workset = first(store)
    revision = 1
    for index in range(1, 50):
        clock.now += 1
        revision = store.update(workset, revision, "x", state(row(f"/s/{index}.wav"))).revision
    # Autosave inside the interval overwrites; only the first state was checkpointed.
    assert [i["revision"] for i in store.recovery(workset)["items"]] == [1]
    for _ in range(4):
        clock.now += CHECKPOINT_INTERVAL_SECONDS + 1
        revision = store.update(workset, revision, "x", state(row(f"/s/r{revision}.wav"))).revision
    items = store.recovery(workset)["items"]
    assert len(items) == RECOVERY_GENERATIONS
    assert [i["revision"] for i in items] == sorted((i["revision"] for i in items), reverse=True)
    target = items[-1]["revision"]
    with pytest.raises(WorksetError, match="revision_conflict"):
        store.restore_recovery(workset, target, revision - 1)
    current_before = store.get(workset)
    restored = store.restore_recovery(workset, target, revision)
    assert restored.revision == revision + 1
    # The replaced state becomes a recovery checkpoint, so the restore can be undone.
    assert store.recovery(workset)["items"][0]["revision"] == current_before.revision
    with pytest.raises(WorksetError, match="recovery_not_found"):
        store.restore_recovery(workset, 999_999, restored.revision)


def test_recovery_budget_drops_only_old_checkpoints(store, clock, monkeypatch):
    import utteran_gui.worksets_store as module

    monkeypatch.setattr(module, "RECOVERY_BUDGET_BYTES", 600)
    workset = first(store)
    revision = 1
    for index in range(6):
        clock.now += CHECKPOINT_INTERVAL_SECONDS + 1
        revision = store.update(workset, revision, "x", state(row(f"/s/{index}.wav"))).revision
    sizes = sum(i["state_bytes"] for i in store.recovery(workset)["items"])
    assert sizes <= 600 and store.get(workset).revision == revision


def test_delete_removes_recovery_checkpoints_and_secure_deletes(store, clock, tmp_path):
    keep = first(store)
    _, extra = store.create("synthetic-create-0001", "消す", state(row("/s/" + SENTINEL + ".wav")))
    clock.now += CHECKPOINT_INTERVAL_SECONDS + 1
    store.update(extra.id, 1, "消す", state(row("/s/other.wav")))
    store.delete(extra.id, 2)
    assert [i["id"] for i in store.list()["items"]] == [keep]
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT count(*) FROM recovery WHERE workset_id=?", (extra.id,)).fetchone()[
                0
            ]
            == 0
        )
    # secure_delete: the deleted private value is not left in free pages of the file.
    assert SENTINEL.encode() not in store.path.read_bytes()


# ---- durability and failure handling -------------------------------------------------------


def test_crash_before_commit_keeps_the_previous_state(store):
    workset = first(store)
    before = store.path.read_bytes()
    script = """
import os, sys, json
from pathlib import Path
from utteran_gui.worksets_store import WorksetStore
store = WorksetStore(Path(sys.argv[1]))
original = store._compact
def crash(db, **kwargs):
    os._exit(73)
store._compact = crash
value = json.loads(sys.stdin.read())
store.update(sys.argv[2], 1, "x", value)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(store.path), workset],
        input=json.dumps(state(row("/s/crash.wav"))),
        text=True,
        capture_output=True,
    )
    assert child.returncode == 73, child.stderr
    restarted = WorksetStore(store.path)
    assert restarted.get(workset).revision == 1
    assert restarted.recovery(workset)["items"] == []
    assert restarted.update(workset, 1, "x", state(row("/s/ok.wav"))).revision == 2
    assert len(before) > 0


@pytest.mark.parametrize(
    "code,expected,status",
    [
        (sqlite3.SQLITE_FULL, "store_full", 507),
        (sqlite3.SQLITE_READONLY, "store_readonly", 503),
        (sqlite3.SQLITE_IOERR, "store_unavailable", 503),
        (sqlite3.SQLITE_BUSY, "store_busy", 503),
        (sqlite3.SQLITE_CORRUPT, "store_corrupt", 503),
    ],
)
def test_failure_injection_rolls_back_with_fixed_codes(store, monkeypatch, code, expected, status):
    workset = first(store)
    original = WorksetStore._compact

    def fail(db, **kwargs):
        error = sqlite3.OperationalError(SENTINEL)
        error.sqlite_errorcode = code  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(WorksetStore, "_compact", staticmethod(fail))
    with pytest.raises(WorksetError) as error:
        store.update(workset, 1, "x", state(row("/s/" + SENTINEL + ".wav")))
    assert (error.value.code, error.value.status) == (expected, status)
    assert SENTINEL not in str(error.value)
    assert error.value.retryable is (expected in {"store_busy", "store_unavailable"})
    monkeypatch.setattr(WorksetStore, "_compact", staticmethod(original))
    assert store.get(workset).revision == 1 and store.recovery(workset)["items"] == []


def test_busy_store_is_typed(store):
    impatient = WorksetStore(store.path, timeout=0.01)
    with store._transaction(), pytest.raises(WorksetError, match="store_busy"):
        impatient.update(first(store), 1, "x", empty_state())


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permission semantics")
def test_read_only_file_fails_closed_and_reads_still_work(store):
    workset = first(store)
    store.path.chmod(0o400)
    try:
        with pytest.raises(WorksetError, match="store_readonly"):
            WorksetStore(store.path).update(workset, 1, "x", state(row("/s/a.wav")))
        assert WorksetStore(store.path).get(workset).revision == 1
    finally:
        store.path.chmod(0o600)


@pytest.mark.parametrize(
    "content", [b"private-corruption" * 100, b"SQLite format 3\x00" + b"\x01" * 200]
)
def test_corrupt_file_is_refused_and_preserved(tmp_path, content):
    path = tmp_path / "ws" / "worksets.sqlite3"
    path.parent.mkdir()
    path.write_bytes(content)
    for operation in (lambda s: s.list(), lambda s: s.bootstrap("x")):
        with pytest.raises(WorksetError) as error:
            operation(WorksetStore(path))
        assert error.value.code in {"store_corrupt", "store_unavailable"}
    assert path.read_bytes() == content


def test_unknown_schema_and_bad_records_fail_closed(store):
    workset = first(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE worksets SET state='{\"schema_version\":2}' WHERE id=?", (workset,))
    with pytest.raises(WorksetError, match="store_corrupt"):
        WorksetStore(store.path).get(workset)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=99")
    before = store.path.read_bytes()
    for operation in (lambda s: s.list(), lambda s: s.bootstrap("x")):
        with pytest.raises(WorksetError, match="store_schema_unsupported"):
            operation(WorksetStore(store.path))
    assert store.path.read_bytes() == before


def test_disappeared_store_is_not_recreated_silently(store):
    workset = first(store)
    opened = WorksetStore(store.path)
    opened.get(workset)
    store.path.unlink()
    with pytest.raises(WorksetError):
        opened.update(workset, 1, "x", empty_state())
    assert not store.path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_permissions_and_symlink_refusal(store, tmp_path):
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(WorksetError, match="store_unsafe_path"):
        WorksetStore(link / "w.sqlite3").bootstrap("x")


def test_backup_uses_the_sqlite_api_and_is_consistent(store, tmp_path):
    workset = first(store)
    store.update(workset, 1, "x", state(row("/s/a.wav")))
    copy = tmp_path / "backup.sqlite3"
    store.backup(copy)
    restored = WorksetStore(copy)
    assert restored.get(workset).revision == 2
    if os.name != "nt":
        assert copy.stat().st_mode & 0o777 == 0o600


# ---- capacity: repeated upserts do not grow the file ---------------------------------------


def test_repeated_upserts_do_not_grow_linearly(store, clock):
    workset = first(store)
    glossary = ["語" * 250 + f"{i:04d}" for i in range(MAX_GLOSSARY_TERMS)]
    value = state(
        *[
            row(f"C:/synthetic/{i:03d}-" + "x" * 200 + ".wav", glossary=0)
            for i in range(MAX_INPUTS)
        ],
        glossaries=[glossary],
    )
    revision = store.update(workset, 1, "x", value).revision
    pages_after_first = store.physical()["page_count"]
    samples = []
    for index in range(1000):
        clock.now += 1
        value["inputs"][index % MAX_INPUTS]["selected"] = index % 2 == 0
        value["common"]["output_dir"] = f"C:/synthetic/out-{index}"
        revision = store.update(workset, revision, "x", value).revision
        if index in (99, 499, 999):
            samples.append(store.physical())
    assert revision == 1002
    # Bounded by about two copies of one state, independent of the number of writes.
    assert samples[0]["page_count"] == samples[-1]["page_count"] <= 2 * pages_after_first + 16
    assert samples[-1]["recovery_rows"] <= RECOVERY_GENERATIONS
    assert not store.path.with_name(store.path.name + "-journal").exists()
    assert not store.path.with_name(store.path.name + "-wal").exists()


def test_many_small_upserts_stay_small(store, clock):
    workset = first(store)
    revision = 1
    for index in range(5000):
        clock.now += 0.2
        revision = store.update(workset, revision, "x", state(row(f"/s/{index}.wav"))).revision
    physical = store.physical()
    assert physical["page_count"] <= 16 and physical["file_bytes"] <= 64 * 1024


def test_deletion_returns_space_to_the_file_system(store, clock):
    big = state(*[row("C:/" + "x" * 20000 + f"{i}.wav") for i in range(50)])
    _, extra = store.create("synthetic-create-0001", "big", big)
    grown = store.physical()["page_count"]
    store.delete(extra.id, 1)
    after = store.physical()
    assert after["freelist_count"] == 0 and after["page_count"] < grown / 4


# ---- input existence check -----------------------------------------------------------------


def test_input_status_classifies_local_paths_without_following_links(tmp_path):
    present = tmp_path / "a.wav"
    present.write_bytes(b"\0")
    paths = [
        str(present),
        str(tmp_path / "missing.wav"),
        str(tmp_path),
        "relative.wav",
        "\\\\server\\share\\x.wav",
        "//server/share/x.wav",
    ]
    expected = ["file", "missing", "not_file", "unchecked", "unchecked", "unchecked"]
    if os.name != "nt":
        link = tmp_path / "link.wav"
        link.symlink_to(present)
        paths += [str(link), str(tmp_path / ".." / tmp_path.name / "a.wav")]
        expected += ["unchecked", "unchecked"]  # a link is not followed; ".." is not resolved
    assert input_status(paths) == expected


class _NoSystemCall:
    """Fails the test if any file-system call is attempted for the given path."""

    def __init__(self, monkeypatch, module, marker: str) -> None:
        self.calls: list[str] = []
        for name in ("lstat", "stat"):
            original = getattr(module.os, name)

            def guarded(path, *args, _original=original, **kwargs):
                if marker in str(path):
                    self.calls.append(str(path))
                    raise AssertionError("network path touched")
                return _original(path, *args, **kwargs)

            monkeypatch.setattr(module.os, name, guarded)


def test_input_status_never_stats_a_windows_network_drive(monkeypatch):
    import utteran_gui.worksets_api as module

    drives = {"Z:\\": 4, "C:\\": 3, "D:\\": 5, "Q:\\": 1, "R:\\": 0}  # REMOTE FIXED CDROM ...
    monkeypatch.setattr(module, "_IS_WINDOWS", True)
    monkeypatch.setattr(module, "_drive_type", lambda root: drives[root])
    guard = _NoSystemCall(monkeypatch, module, "synthetic-share")
    states = module.input_status(
        [
            "Z:\\synthetic-share\\x.wav",
            "D:\\synthetic-share\\x.wav",
            "Q:\\synthetic-share\\x.wav",
            "R:\\synthetic-share\\x.wav",
            "\\\\?\\UNC\\synthetic-share\\x.wav",
        ]
    )
    assert states == ["unchecked"] * 5
    assert guard.calls == []
    # A fixed drive is read component by component with lstat (links are never followed).
    seen: list[str] = []
    regular = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, 1, 0, 0, 0))
    folder = os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    def fake_lstat(path, *args, **kwargs):
        seen.append(str(path))
        return regular if str(path).endswith(".wav") else folder

    monkeypatch.setattr(module.os, "lstat", fake_lstat)
    assert module.input_status(["C:\\synthetic-local\\x.wav"]) == ["file"]
    assert seen == ["C:\\synthetic-local", "C:\\synthetic-local\\x.wav"]
    # A reparse point (junction/symlink) on the way is never followed.
    seen.clear()
    junction = os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(module, "_is_reparse", lambda info: info is junction)
    monkeypatch.setattr(
        module.os, "lstat", lambda path, *a, **k: seen.append(str(path)) or junction
    )
    assert module.input_status(["C:\\synthetic-link\\x.wav"]) == ["unchecked"]
    assert seen == ["C:\\synthetic-link"]


@pytest.mark.skipif(os.name == "nt", reason="the Windows path is covered by the drive-type test")
def test_input_status_never_stats_a_network_mount(tmp_path, monkeypatch):
    import utteran_gui.worksets_api as module

    share = tmp_path / "synthetic-share"
    share.mkdir()
    (share / "a.wav").write_bytes(b"\0")
    for fstype in ("nfs4", "cifs", "fuse.sshfs", "9p"):
        monkeypatch.setattr(module, "_mounts", lambda f=fstype: [("/", "ext4"), (str(share), f)])
        guard = _NoSystemCall(monkeypatch, module, "synthetic-share")
        assert module.input_status([str(share / "a.wav")]) == ["unchecked"], fstype
        assert guard.calls == []
    monkeypatch.setattr(module, "_mounts", lambda: [])
    assert module.input_status([str(tmp_path)]) == ["unchecked"]  # no mount table: unproven


def test_input_status_bounds_threads_and_stops_after_the_deadline(tmp_path, monkeypatch):
    import utteran_gui.worksets_api as module

    present = tmp_path / "a.wav"
    present.write_bytes(b"\0")
    release = threading.Event()
    seen: list[str] = []
    original = module.os.lstat

    def hanging(path, *args, **kwargs):
        seen.append(str(path))
        if str(path).endswith("hang.wav"):
            release.wait(10)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.os, "lstat", hanging)
    before = threading.active_count()
    hung = [str(tmp_path / "hang.wav"), str(present)]
    # Every worker slot hangs on one call; later paths of the same request are never started.
    for _ in range(module.INPUT_STATUS_MAX_WORKERS):
        assert module.input_status(hung, deadline=0.2) == ["unchecked", "unchecked"]
    # No slot is free: answered at once, no new thread, no system call.
    calls = len(seen)
    assert module.input_status([str(present)], deadline=5) == ["unchecked"]
    assert len(seen) == calls
    assert threading.active_count() <= before + module.INPUT_STATUS_MAX_WORKERS
    assert not any(p == str(present) for p in seen)
    release.set()
    for _ in range(100):
        if module.input_status([str(present)], deadline=1) == ["file"]:
            break
        threading.Event().wait(0.05)
    else:
        raise AssertionError("worker slots were not released")


def test_input_status_api_does_not_echo_paths(client, tmp_path):
    present = tmp_path / (SENTINEL + ".wav")
    present.write_bytes(b"\0")
    answer = client.post(
        "/api/v1/worksets/input-status",
        json={"schema_version": 1, "paths": [str(present), str(tmp_path / "gone.wav")]},
    )
    assert answer.status_code == 200
    assert answer.json() == {
        "schema_version": 1,
        "items": [{"state": "file"}, {"state": "missing"}],
    }
    assert SENTINEL not in answer.text
    too_many = client.post(
        "/api/v1/worksets/input-status", json={"schema_version": 1, "paths": ["/a"] * 101}
    )
    assert too_many.status_code == 422


# ---- privacy -------------------------------------------------------------------------------


def test_errors_and_logs_carry_no_private_values(client, caplog):
    caplog.set_level(logging.DEBUG)
    workset = boot(client, SENTINEL)["items"][0]["id"]
    url = f"/api/v1/worksets/{workset}"
    private = state(row("/s/" + SENTINEL + ".wav", glossary=0), glossaries=[[SENTINEL]])
    answers = [
        client.put(
            url,
            json={"schema_version": 1, "name": SENTINEL, "state": private},
            headers={"If-Match": '"7"'},
        ),
        client.put(
            url,
            json={"schema_version": 1, "name": SENTINEL + "\n", "state": private},
            headers={"If-Match": '"1"'},
        ),
        client.put(
            url,
            json={"schema_version": 1, "name": "x", "state": {**private, "extra": SENTINEL}},
            headers={"If-Match": '"1"'},
        ),
        client.post(
            "/api/v1/worksets",
            json={"schema_version": 1, "name": SENTINEL},
            headers={"Idempotency-Key": SENTINEL + "/"},
        ),
        client.put(
            url,
            content=b'{"name": "' + SENTINEL.encode() + b'"',
            headers={"If-Match": '"1"', "Content-Type": "application/json"},
        ),
    ]
    assert [a.status_code for a in answers] == [412, 422, 422, 400, 400]
    for answer in answers:
        assert SENTINEL not in answer.text
    assert (
        client.put(
            url,
            json={"schema_version": 1, "name": "x", "state": private},
            headers={"If-Match": '"1"'},
        ).status_code
        == 200
    )
    assert SENTINEL not in caplog.text


def test_worksets_modules_do_not_log_or_add_handlers():
    for name in ("worksets_api.py", "worksets_store.py", "worksets_model.py"):
        text = (ROOT / "src/utteran_gui" / name).read_text(encoding="utf-8")
        assert "logging" not in text and "print(" not in text and "FileHandler" not in text


def test_gui_log_rotation_limits_are_unchanged():
    text = (ROOT / "src/utteran_gui/logging_runtime.py").read_text(encoding="utf-8")
    assert "RotatingFileHandler" in text and "backupCount=5" in text
    assert "10 * 1024 * 1024" in text


# ---- schema document -----------------------------------------------------------------------


def test_published_state_schema_is_current():
    generated = json.dumps(state_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    assert SCHEMA.read_text(encoding="utf-8") == generated
