"""Windows product gate, including absent, broken and guarantee-reporting wheels."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_b7_api import AUTHORITY
from test_b7_jobs import SESSION
from test_ec_reexport import World, _app, tree
from utteran_gui import reexport_files_native as native
from utteran_gui import reexport_service
from utteran_gui.api import SESSION_HEADER


@pytest.mark.parametrize("wheel", ["missing", "broken", "capable"])
def test_windows_reexport_api_is_unsupported_without_writes(tmp_path, monkeypatch, wheel):
    def capabilities():
        if wheel == "broken":
            raise OSError("cannot load")
        return {"ancestors_pinned": "enforced", "dir_creation": "atomic"}

    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        native,
        "_native",
        None if wheel == "missing" else SimpleNamespace(capabilities=capabilities),
    )
    assert native.supported() is False
    monkeypatch.setattr(reexport_service, "reexport_files", native)
    world = World(tmp_path)
    world.service._supported = native.supported
    job_id = world.finished()
    before = tree(world.output), tree(world.inputs)
    with TestClient(
        _app(world), base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}
    ) as client:
        plan = client.get(f"/api/v1/jobs/{job_id}/reexport-source")
        assert plan.status_code == 200
        assert plan.json()["refusal"] == "reexport_platform_unsupported"
        response = client.post(
            f"/api/v1/jobs/{job_id}/reexports",
            headers={
                "Origin": "http://" + AUTHORITY,
                "If-Match": f'"{world.store.get_job(job_id).revision}"',
                "Idempotency-Key": "windows-disabled-0001",
            },
            json={
                "schema_version": 1,
                "source_sha256": "a" * 64,
                "formats": ["txt"],
                "show_speaker": True,
                "srt_bom": False,
                "newline": "lf",
                "speaker_labels": {},
                "output_parent": None,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "reexport_platform_unsupported"
    assert (tree(world.output), tree(world.inputs)) == before
