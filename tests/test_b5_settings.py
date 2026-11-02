from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from utteran_contracts.editing import EditingSession, SettingOverride
from utteran_contracts.preview import ValidationResult
from utteran_contracts.preview_schema import schema_bytes
from utteran_contracts.settings import Policy, PolicyWrite, Preset, PresetWrite
from utteran_gui.api import SESSION_COOKIE, SESSION_HEADER, create_app
from utteran_gui.settings_api import MAX_BODY
from utteran_paths import resolve_data_paths
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import preview
from utteran_settings.repository import Repository

ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"
KEY = "synthetic-key-0001"


@pytest.fixture
def examples():
    return json.loads(
        (ROOT / "docs/architecture/phase-a/examples.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def repository(tmp_path):
    return Repository(tmp_path / "private" / "resources.sqlite3")


def create(repository, examples, kind="presets", key=KEY):
    payload = (PresetWrite if kind == "presets" else PolicyWrite).model_validate(
        examples["preset" if kind == "presets" else "policy"]
    )
    return repository.post("/api/v1/" + kind, key, payload.to_wire(), payload=payload)


def objects(examples):
    preset = Preset.model_validate({**examples["preset"], "id": "pre_test", "revision": 1})
    policy = Policy.model_validate({**examples["policy"], "id": "pol_test", "revision": 1})
    session = {
        "schema_version": 1,
        "preset_ref": {"id": preset.id, "revision": preset.revision},
        "policy_ref": {"id": policy.id, "revision": policy.revision},
        "common_overrides": [],
        "files": [
            {
                "client_file_id": "one",
                "input_path": "private.wav",
                "output_dir": "private",
                "overrides": [],
            },
            {
                "client_file_id": "two",
                "input_path": "other.wav",
                "output_dir": "private",
                "overrides": [],
            },
        ],
    }
    return preset, policy, session


def effective(examples, common=(), file=()):
    preset, policy, session = objects(examples)
    session["common_overrides"] = list(common)
    session["files"][0]["overrides"] = list(file)
    dto = EditingSession.model_validate(session)
    before = dto.to_wire(), preset.to_wire(), policy.to_wire()
    result = preview(dto, preset, policy)
    assert before == (dto.to_wire(), preset.to_wire(), policy.to_wire())
    return result


@pytest.mark.parametrize("kind", ["presets", "execution-policies"])
def test_history_tombstone_and_replay(repository, examples, kind):
    status, headers, body = create(repository, examples, kind)
    assert status == 201 and headers["ETag"] == '"1"'
    model = PresetWrite if kind == "presets" else PolicyWrite
    payload = model.model_validate(examples["preset" if kind == "presets" else "policy"])
    updated = repository.mutate(kind, body["id"], 1, payload.model_copy(update={"name": "new"}))
    assert updated.revision == 2
    assert repository.get(kind, body["id"], 1).name == body["name"]
    assert repository.list(kind)["items"][0]["revision"] == 2
    repository.mutate(kind, body["id"], 2, None)
    with pytest.raises(SettingsError, match="resource_not_found"):
        repository.get(kind, body["id"])
    assert repository.list(kind)["items"] == []
    assert repository.get(kind, body["id"], 1).revision == 1
    with pytest.raises(SettingsError):
        repository.get(kind, body["id"], 3)
    restarted = Repository(repository.path)
    assert create(restarted, examples, kind) == (status, headers, body)
    changed = deepcopy(examples)
    changed["preset" if kind == "presets" else "policy"]["name"] = "conflict"
    with pytest.raises(SettingsError, match="idempotency_conflict"):
        create(restarted, changed, kind)
    other = "execution-policies" if kind == "presets" else "presets"
    with pytest.raises(SettingsError):
        repository.get(other, body["id"], 1)


def test_atomic_create_and_cas(repository, examples):
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: create(Repository(repository.path), examples), range(2)))
    assert results[0] == results[1]
    assert len(repository.list("presets")["items"]) == 1
    resource_id = results[0][2]["id"]

    def update(_):
        try:
            repository.mutate(
                "presets", resource_id, 1, PresetWrite.model_validate(examples["preset"])
            )
            return 200
        except SettingsError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(update, range(2))) == [200, 412]


def test_snapshot_cursor_survives_updates(repository, examples):
    for n in range(6):
        create(repository, examples, key=f"synthetic-{n:016}")
    first = repository.list("presets", limit=2)
    original = repository.list("presets")["items"]
    last_id = original[-1]["id"]
    repository.mutate("presets", last_id, 1, None)
    create(repository, examples, key="synthetic-new-0001")
    second = repository.list("presets", first["next_cursor"], limit=2)
    third = repository.list("presets", second["next_cursor"], limit=2)
    assert first["items"] + second["items"] + third["items"] == original
    assert {page["revision"] for page in (first, second, third)} == {first["revision"]}
    assert third["next_cursor"] is None
    for cursor in ("bad", first["next_cursor"] + "x"):
        with pytest.raises(SettingsError, match="cursor_invalid"):
            repository.list("presets", cursor)
    with pytest.raises(SettingsError):
        repository.list("execution-policies", first["next_cursor"])


def test_failed_transaction_is_not_partial(repository, examples):
    with pytest.raises(RuntimeError), repository._transaction() as db:
        repository._append(
            db, "presets", "pre_rollback", 1, PresetWrite.model_validate(examples["preset"])
        )
        raise RuntimeError("synthetic power interruption before commit")
    assert repository.list("presets")["revision"] == 1
    assert repository.list("presets")["items"] == []
    with sqlite3.connect(repository.path) as db:
        assert db.execute("SELECT count(*) FROM replays").fetchone()[0] == 0


@pytest.mark.parametrize(
    "code,reason",
    [
        (sqlite3.SQLITE_FULL, "store_full"),
        (sqlite3.SQLITE_READONLY, "store_readonly"),
        (sqlite3.SQLITE_IOERR, "store_unavailable"),
    ],
)
def test_failure_injection_rolls_back(repository, examples, monkeypatch, code, reason):
    original = repository._append

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        error = sqlite3.OperationalError("private sentinel")
        error.sqlite_errorcode = code
        raise error

    monkeypatch.setattr(repository, "_append", fail)
    with pytest.raises(SettingsError, match=reason) as error:
        create(repository, examples)
    assert "private sentinel" not in str(error.value)
    assert repository.list("presets")["items"] == []
    monkeypatch.setattr(repository, "_append", original)
    assert create(repository, examples)[0] == 201


def test_busy_is_typed(repository, examples):
    impatient = Repository(repository.path, timeout=0.01)
    with repository._transaction(), pytest.raises(SettingsError, match="store_busy"):
        create(impatient, examples)


@pytest.mark.parametrize("content", [b"private-corruption", b""])
def test_bad_existing_database_preserved(tmp_path, content):
    path = tmp_path / "private" / "bad.sqlite3"
    path.parent.mkdir()
    path.write_bytes(content)
    with pytest.raises(SettingsError):
        Repository(path)
    assert path.read_bytes() == content


def test_unknown_schema_preserved(repository):
    with sqlite3.connect(repository.path) as db:
        db.execute("PRAGMA user_version=99")
    before = repository.path.read_bytes()
    with pytest.raises(SettingsError, match="store_schema_unsupported"):
        Repository(repository.path)
    assert repository.path.read_bytes() == before


def test_permissions_and_roots(repository, monkeypatch, tmp_path):
    if os.name != "nt":
        assert repository.path.stat().st_mode & 0o777 == 0o600
        assert repository.path.parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv("UTTERAN_DATA_ROOT", raising=False)
    normal = resolve_data_paths(tmp_path).settings_resources
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(tmp_path / "portable"))
    portable = resolve_data_paths(ROOT).settings_resources
    assert portable == tmp_path / "portable/data/settings-v1/resources.sqlite3"
    assert normal != portable and not normal.is_relative_to(ROOT)


@pytest.mark.parametrize("value", [None, "", "auto", "private prompt"])
def test_prompt_states(examples, value):
    result = effective(examples, [{"path": "/asr/initial_prompt", "value": value}])
    file = result.files[0]
    assert file.effective_settings.asr.initial_prompt == (None if value == "" else value)
    provenance = next(p for p in file.provenance if p.path == "/asr/initial_prompt")
    assert provenance.source == ("normalization" if value == "" else "common")
    assert provenance.input_source == "common"
    assert provenance.input_status == (
        "null" if value is None else "empty" if value == "" else "value"
    )


def test_override_follow_and_remove(examples):
    common = [{"path": "/asr/beam_size", "value": 8}]
    file = [{"path": "/asr/beam_size", "value": 12}]
    result = effective(examples, common, file)
    assert [f.effective_settings.asr.beam_size for f in result.files] == [12, 8]
    common[0]["value"] = 9
    assert [
        f.effective_settings.asr.beam_size for f in effective(examples, common, file).files
    ] == [12, 9]
    assert effective(examples, common).files[0].effective_settings.asr.beam_size == 9
    assert effective(examples).files[0].effective_settings.asr.beam_size == 5
    for f, source in zip(result.files, ("file", "common"), strict=True):
        assert next(p for p in f.provenance if p.path == "/asr/beam_size").source == source


def test_atomic_leaves_false_auto_and_empty(examples):
    result = effective(
        examples,
        [
            {"path": "/asr/glossary", "value": ["synthetic"]},
            {"path": "/output/speaker_labels", "value": {"speaker": "synthetic"}},
            {"path": "/asr/backend", "value": "auto"},
            {"path": "/asr/model", "value": "auto"},
            {"path": "/asr/vad/enabled", "value": False},
            {"path": "/output/formats", "value": ["txt"]},
        ],
        [{"path": "/asr/glossary", "value": []}, {"path": "/output/speaker_labels", "value": {}}],
    )
    a, b = result.files
    assert a.effective_settings.asr.glossary == ()
    assert dict(a.effective_settings.output.speaker_labels) == {}
    assert dict(b.effective_settings.output.speaker_labels) == {"speaker": "synthetic"}
    assert a.effective_settings.asr.backend == "auto" and a.effective_settings.asr.model == "auto"
    assert a.effective_settings.asr.vad.enabled is False
    assert a.effective_settings.output.formats == ("txt",)
    assert a.worker_support == "unverified" and b.worker_support == "unsupported"


@pytest.mark.parametrize(
    "path,value",
    [
        ("/asr/beam_size", None),
        ("/asr/beam_size", True),
        ("/asr/beam_size", "5"),
        ("/output/formats/0", "srt"),
        ("/output/formats", []),
        ("/output/speaker_labels/key", "name"),
        ("/schema_version", 1),
        ("/asr", {}),
        ("/asr/unknown", 1),
        ("/output/formats", ["srt", "srt"]),
    ],
)
def test_bad_override_rejected(path, value):
    with pytest.raises(ValidationError):
        SettingOverride(path=path, value=value)


@pytest.mark.parametrize("mutation", ["empty", "duplicate_file", "duplicate_override", "version"])
def test_editing_shape_errors(examples, mutation):
    _, _, data = objects(examples)
    if mutation == "empty":
        data["files"] = []
    elif mutation == "duplicate_file":
        data["files"][1] = data["files"][0]
    elif mutation == "duplicate_override":
        data["common_overrides"] = [{"path": "/asr/beam_size", "value": 5}] * 2
    else:
        data["schema_version"] = 2
    with pytest.raises(ValidationError):
        EditingSession.model_validate(data)


def test_backend_and_revision_switch_revalidates(examples):
    preset, policy, session = objects(examples)
    session["common_overrides"] = [{"path": "/asr/faster_whisper/compute_type", "value": "int8"}]
    settings = preset.settings.to_wire()
    settings["asr"].pop("faster_whisper")
    settings["asr"]["backend"] = "whisper-cpp"
    switched = Preset.model_validate(
        {**preset.to_wire(), "id": "pre_new", "revision": 2, "settings": settings}
    )
    session["preset_ref"] = {"id": switched.id, "revision": 2}
    result = preview(EditingSession.model_validate(session), switched, policy)
    assert not result.valid
    assert result.files[0].issues[0].code == "incompatible_override"
    assert result.files[0].issues[0].path == "/asr/faster_whisper/compute_type"
    session["preset_ref"]["revision"] = 1
    with pytest.raises(SettingsError, match="reference_unresolved"):
        preview(EditingSession.model_validate(session), switched, policy)


@pytest.mark.parametrize(
    "common,code",
    [
        (
            [
                {"path": "/diarization/min_speakers", "value": 4},
                {"path": "/diarization/max_speakers", "value": 2},
            ],
            "contract_validation_failed",
        ),
        (
            [
                {"path": "/diarization/enabled", "value": True},
                {"path": "/asr/word_timestamps", "value": "never"},
            ],
            "diarization_requires_timestamps",
        ),
    ],
)
def test_cross_fields(examples, common, code):
    result = effective(examples, common)
    assert not result.valid and result.files[0].issues[0].code == code


def test_genai_language_and_worker_are_separate(examples):
    examples["preset"]["settings"]["asr"].pop("faster_whisper")
    examples["preset"]["settings"]["asr"]["backend"] = "openvino-genai"
    valid = effective(examples)
    assert valid.valid and valid.files[0].worker_support == "unsupported"
    invalid = effective(examples, [{"path": "/diarization/enabled", "value": True}])
    assert not invalid.valid and invalid.files[0].issues[0].code == "genai_diarization_language"


@pytest.mark.parametrize(
    "tokens,allow,code",
    [
        (["--beam-size=8"], False, "argument_conflict"),
        (["--entropy-thold", "2.6"], False, "argument_conflict"),
        (["-t", "4"], True, "argument_conflict"),
        (["@private"], True, "argument_unverified"),
        (["--model", "private"], True, "argument_unverified"),
        (["--new-native-option"], False, "argument_unverified"),
    ],
)
def test_extra_args_fail_closed(examples, tokens, allow, code):
    examples["preset"]["settings"]["asr"].pop("faster_whisper")
    examples["preset"]["settings"]["asr"].update(
        backend="whisper-cpp",
        extra_arguments={"backend": "whisper-cpp", "tokens": tokens, "allow_unverified": allow},
    )
    result = effective(examples)
    assert not result.valid and result.files[0].issues[0].code == code


@pytest.fixture
def client(tmp_path):
    app = create_app(
        "synthetic-session",
        repo_root=ROOT,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=tmp_path / "db" / "resources.sqlite3",
    )
    with TestClient(
        app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: "synthetic-session"}
    ) as client:
        yield client


def post(client, kind, body, key=KEY):
    return client.post("/api/v1/" + kind, json=body, headers={"Idempotency-Key": key})


@pytest.mark.parametrize("kind", ["presets", "execution-policies"])
def test_http_resource_contract(client, examples, kind):
    payload = examples["preset" if kind == "presets" else "policy"]
    response = post(client, kind, payload)
    assert response.status_code == 201, response.text
    location = response.headers["Location"]
    assert response.headers["ETag"] == '"1"'
    assert post(client, kind, payload).json() == response.json()
    assert client.get(location).json() == response.json()
    assert client.put(location, json=payload).status_code == 428
    assert client.put(location, json=payload, headers={"If-Match": "*"}).status_code == 400
    assert client.put(location, json=payload, headers={"If-Match": '"1"'}).headers["ETag"] == '"2"'
    assert client.delete(location, headers={"If-Match": '"1"'}).status_code == 412
    assert client.delete(location, headers={"If-Match": '"2"'}).status_code == 204
    assert client.get(location).status_code == 404
    assert client.get(location + "?revision=1").json() == response.json()
    assert client.get(location + "?revision=0").status_code == 422
    assert post(client, kind, {**payload, "name": "different"}).status_code == 409


def test_http_validation_replay_and_no_execution(client, examples, monkeypatch):
    p = post(client, "presets", examples["preset"]).json()
    q = post(client, "execution-policies", examples["policy"]).json()
    _, _, session = objects(examples)
    session["preset_ref"] = {"id": p["id"], "revision": 1}
    session["policy_ref"] = {"id": q["id"], "revision": 1}

    def forbidden(*args, **kwargs):
        raise AssertionError("preview attempted external work")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = post(client, "validations", session)
    assert result.status_code == 200, result.text
    dto = ValidationResult.model_validate(result.json())
    assert dto.valid and dto.preset_ref.id == p["id"]
    client.delete("/api/v1/presets/" + p["id"], headers={"If-Match": '"1"'})
    assert post(client, "validations", session).json() == result.json()
    assert post(client, "validations", session, key="another-key-0001").status_code == 200
    session["preset_ref"]["revision"] = 99
    assert post(client, "validations", session).status_code == 409
    assert post(client, "validations", session, key="unresolved-key-0001").status_code == 422


@pytest.mark.parametrize(
    "headers,status",
    [
        ({SESSION_HEADER: "wrong"}, 401),
        ({"Origin": "http://evil.example"}, 403),
        ({"Host": "evil.example"}, 403),
        ({"Origin": "null"}, 403),
    ],
)
def test_auth_origin_host(client, headers, status):
    response = client.get("/api/v1/presets", headers=headers)
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"


def test_cookie_csrf_and_legacy(client, examples):
    client.headers.pop(SESSION_HEADER)
    assert client.get("/api/v1/presets").status_code == 401
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    assert client.get("/api/v1/presets").status_code == 200
    assert post(client, "presets", examples["preset"]).status_code == 403
    assert (
        client.post(
            "/api/v1/presets",
            json=examples["preset"],
            headers={"Origin": "http://" + AUTHORITY, "Idempotency-Key": KEY},
        ).status_code
        == 201
    )
    assert client.get("/api/health").status_code == 200
    client.cookies.clear()
    assert client.get("/api/health").status_code == 401


def test_errors_do_not_echo_private_values(client, examples, caplog):
    sentinel = "SYNTHETIC_PRIVATE_B5_SENTINEL"
    payload = deepcopy(examples["preset"])
    payload["settings"]["asr"]["initial_prompt"] = sentinel
    payload[sentinel] = sentinel
    response = post(client, "presets", payload)
    assert response.status_code == 422 and sentinel not in response.text
    payload.pop(sentinel)
    payload["schema_version"] = 2
    response = post(client, "presets", payload)
    assert response.status_code == 422 and sentinel not in response.text
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    "body,headers,status",
    [
        ("{", {"Content-Type": "application/json", "Idempotency-Key": KEY}, 400),
        ('{"x":1,"x":2}', {"Content-Type": "application/json", "Idempotency-Key": KEY}, 400),
        ('{"x":NaN}', {"Content-Type": "application/json", "Idempotency-Key": KEY}, 400),
        ("{}", {"Content-Type": "text/plain", "Idempotency-Key": KEY}, 400),
        ("{}", {"Content-Type": "application/json"}, 400),
        ("{}", {"Content-Type": "application/json", "Idempotency-Key": "short"}, 400),
        (
            "{}",
            {
                "Content-Type": "application/json",
                "Idempotency-Key": KEY,
                "Content-Length": str(MAX_BODY + 1),
            },
            413,
        ),
    ],
)
def test_http_input_failures(client, body, headers, status):
    assert client.post("/api/v1/presets", content=body, headers=headers).status_code == status


def test_chunked_body_limit(client):
    response = client.post(
        "/api/v1/presets",
        content=iter([b" " * MAX_BODY, b"x"]),
        headers={"Content-Type": "application/json", "Idempotency-Key": KEY},
    )
    assert response.status_code == 413


def test_http_semantic_422_has_file_path(client, examples):
    p = post(client, "presets", examples["preset"]).json()
    q = post(client, "execution-policies", examples["policy"]).json()
    _, _, session = objects(examples)
    session["preset_ref"] = {"id": p["id"], "revision": 1}
    session["policy_ref"] = {"id": q["id"], "revision": 1}
    session["files"][0]["overrides"] = [
        {"path": "/diarization/enabled", "value": True},
        {"path": "/asr/word_timestamps", "value": "never"},
    ]
    response = post(client, "validations", session)
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["path"] == "/files/0/asr/word_timestamps"


def test_opt_in_disabled_has_no_store_or_routes(tmp_path):
    path = tmp_path / "db" / "resources.sqlite3"
    app = create_app("synthetic-session", repo_root=ROOT, v1_store_path=path)
    with TestClient(app, headers={SESSION_HEADER: "synthetic-session"}) as client:
        assert client.get("/api/v1/presets").status_code == 404
        assert client.get("/api/health").status_code == 200
    assert not path.exists()


def test_b5_schema_and_phase_a(examples):
    schema = json.loads(schema_bytes())
    jsonschema.Draft202012Validator.check_schema(schema)
    assert (ROOT / "docs/architecture/b5/preview.schema.json").read_bytes() == schema_bytes()
    result = effective(examples).to_wire()
    jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/ValidationResult"}).validate(result)
    proposed = yaml.safe_load(
        (ROOT / "docs/architecture/phase-a/openapi.yaml").read_text(encoding="utf-8")
    )
    for name in ("Provenance", "ValidatedFile", "ValidationResult", "PresetList", "PolicyList"):
        expected = json.loads(
            json.dumps(schema["$defs"][name]).replace("#/$defs/", "#/components/schemas/")
        )
        assert proposed["components"]["schemas"][name] == expected


def test_lightweight_imports_do_not_execute():
    script = """
import sys
import utteran_settings.repository
import utteran_settings.inheritance
import utteran_gui.settings_api
from utteran_contracts.preview_schema import schema_bytes
assert schema_bytes()
assert not any(n == 'utteran' or n.startswith('utteran.') for n in sys.modules)
heavy = {'torch','faster_whisper','openvino','pyannote'}
assert not any(n.split('.')[0] in heavy for n in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)


def test_language_normalization_and_b4_parity(examples):
    from utteran.asr.registry import NON_SPACE_LANGUAGES
    from utteran_settings.inheritance import _NON_SPACE

    assert NON_SPACE_LANGUAGES | {"auto", ""} == _NON_SPACE
    result = effective(examples, [{"path": "/asr/language", "value": " Auto "}])
    assert result.files[0].effective_settings.asr.language == "auto"
    provenance = next(p for p in result.files[0].provenance if p.path == "/asr/language")
    assert provenance.source == "normalization" and provenance.input_source == "common"


def test_replay_checked_before_new_contract_validation(client, examples):
    assert post(client, "presets", examples["preset"]).status_code == 201
    assert post(client, "presets", {"schema_version": 99}).status_code == 409
    assert (
        post(client, "presets", {"schema_version": 99}, "new-schema-key-0001").json()["error"][
            "code"
        ]
        == "schema_version_unsupported"
    )


def test_process_crash_before_commit_recovers(repository, examples):
    # A real process exits after resource append, before replay insertion/commit.
    script = """
import os,sys,json
from utteran_settings.repository import Repository
from utteran_contracts.settings import PresetWrite
repo=Repository(__import__('pathlib').Path(sys.argv[1]))
original=repo._append
def crash(*args,**kwargs):
    original(*args,**kwargs)
    os._exit(73)
repo._append=crash
payload=PresetWrite.model_validate_json(sys.stdin.read())
repo.post('/api/v1/presets', 'synthetic-key-0001', payload.to_wire(), payload=payload)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(repository.path)],
        input=json.dumps(examples["preset"]),
        text=True,
        capture_output=True,
    )
    assert child.returncode == 73
    restarted = Repository(repository.path)
    assert restarted.list("presets")["items"] == []
    created = create(restarted, examples)
    # A fresh interpreter observes the committed resource/replay after restart.
    script = """
import sys,json
from pathlib import Path
from utteran_settings.repository import Repository
from utteran_contracts.settings import PresetWrite
repo=Repository(Path(sys.argv[1]))
p=PresetWrite.model_validate_json(sys.stdin.read())
print(json.dumps(repo.post('/api/v1/presets','synthetic-key-0001',p.to_wire(),payload=p)))
"""
    replay = subprocess.run(
        [sys.executable, "-c", script, str(repository.path)],
        input=json.dumps(examples["preset"]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(replay.stdout) == list(created)


def test_bad_replay_record_is_typed(repository, examples):
    create(repository, examples)
    with sqlite3.connect(repository.path) as db:
        db.execute("UPDATE replays SET body='not-json'")
    with pytest.raises(SettingsError, match="store_corrupt"):
        create(repository, examples)


def test_permission_error_is_typed(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise PermissionError("synthetic private path")

    monkeypatch.setattr(Path, "mkdir", deny)
    with pytest.raises(SettingsError, match="store_unavailable") as error:
        Repository(tmp_path / "secret" / "resources.sqlite3")
    assert "private path" not in str(error.value)


def test_initialization_failure_preserves_existing_assets(tmp_path, monkeypatch):
    import utteran_settings.repository as module

    path = tmp_path / "private" / "resources.sqlite3"
    connect = sqlite3.connect

    class Broken(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement.startswith("CREATE INDEX"):
                raise sqlite3.OperationalError("synthetic migration failure")
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(
        module.sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=Broken)
    )
    with pytest.raises(SettingsError):
        Repository(path)
    assert path.exists()
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    with connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    with pytest.raises(SettingsError, match="store_schema_unsupported"):
        Repository(path)


def test_validation_replay_after_app_restart(tmp_path, examples):
    from utteran_gui.settings_api import create_settings_app

    path = tmp_path / "db" / "resources.sqlite3"

    def start(session):
        # Standalone subapp has paths without /api/v1.
        return TestClient(
            create_settings_app(session, authority=AUTHORITY, store_path=path),
            base_url="http://" + AUTHORITY,
            headers={SESSION_HEADER: session, "Idempotency-Key": KEY},
        )

    with start("session-one") as first:
        p = first.post("/presets", json=examples["preset"]).json()
        q = first.post("/execution-policies", json=examples["policy"]).json()
        _, _, body = objects(examples)
        body["preset_ref"] = {"id": p["id"], "revision": 1}
        body["policy_ref"] = {"id": q["id"], "revision": 1}
        result = first.post("/validations", json=body)
        assert result.status_code == 200
    with start("session-two") as second:
        assert second.post("/validations", json=body).json() == result.json()


@pytest.mark.parametrize("body", [b'{"x":1e999}', b'{"x":"\\ud800"}'])
def test_nonfinite_and_unpaired_surrogates_are_safe_400(client, body):
    result = client.post(
        "/api/v1/presets",
        content=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": KEY},
    )
    assert result.status_code == 400
