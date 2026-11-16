"""Glossary / prompt compilation (0.1.53): versioned, frozen per snapshot, never silent.

Synthetic fixtures only. Inference is replaced; the conversion from preset to the core
Config (B5 -> B7 snapshot -> private IPC -> B3 adapter) is the real code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_b7_b3_model_route import model_files, offline, over_ipc, planned
from test_b7_jobs import SENTINEL, Env
from test_config_adapter import context, settings_data
from utteran.config_adapter import ConfigAdaptationError, adapt_settings
from utteran.jobs import stage_config_hashes
from utteran.worker_runtime import explicit_config
from utteran_contracts import PresetWrite, ProcessingSettings
from utteran_contracts.jobs import PROMPT_COMPILER_VERSION, JobSnapshot
from utteran_contracts.prompting import (
    BIAS_SOFT_LIMIT,
    CURRENT_PROMPT_COMPILER,
    bias_may_be_truncated,
    compile_prompt,
    glossary_terms,
)
from utteran_contracts.worker import WorkerErrorCode, WorkerRequest
from utteran_contracts.worker_framing import ProtocolError
from utteran_gui.job_admission import AdmissionError
from utteran_gui.job_dispatcher import plan_request
from utteran_settings.repository import Repository

__all__ = ["offline", "settings_data"]  # re-exported pytest fixtures
TERMS = ["Utteran", "話者分離", "CTranslate2"]


def test_terms_are_trimmed_deduplicated_and_ordered() -> None:
    assert glossary_terms([" b ", "a", "b", "", "  ", "A"]) == ("b", "a", "A")


def test_a_term_with_line_breaks_cannot_break_the_prompt_layout() -> None:
    """PR #50 review: a pasted term containing a newline or tab stays one line."""
    terms = ["two\nlines", "tab\there", "a  b", "\n", "two lines"]
    assert glossary_terms(terms) == ("two lines", "tab here", "a b")
    compiled = compile_prompt("prompt", terms, "en", compiler="glossary-bias-1")
    assert compiled == "two lines, tab here, a b\nprompt"
    assert compiled.count("\n") == 1


@pytest.mark.parametrize("compiler", ["b2-verbatim", "glossary-bias-1"])
@pytest.mark.parametrize("prompt", [None, "", "synthetic prompt"])
def test_without_glossary_every_version_is_verbatim(compiler: str, prompt: str | None) -> None:
    assert compile_prompt(prompt, (), "ja", compiler=compiler) == prompt
    assert compile_prompt(prompt, ("  ",), "ja", compiler=compiler) == prompt


def test_glossary_bias_format_keeps_the_user_prompt_last() -> None:
    assert compile_prompt(None, TERMS, "ja", compiler="glossary-bias-1") == (
        "Utteran、話者分離、CTranslate2"
    )
    assert compile_prompt("prompt", TERMS, "en", compiler="glossary-bias-1") == (
        "Utteran, 話者分離, CTranslate2\nprompt"
    )
    assert compile_prompt("", TERMS, "auto", compiler="glossary-bias-1") == (
        "Utteran, 話者分離, CTranslate2"
    )


def test_old_or_unknown_compiler_refuses_a_glossary_instead_of_ignoring_it() -> None:
    with pytest.raises(ValueError, match="glossary_requires_compiler"):
        compile_prompt("prompt", TERMS, "ja", compiler="b2-verbatim")
    with pytest.raises(ValueError, match="unsupported_prompt_compiler"):
        compile_prompt("prompt", (), "ja", compiler="glossary-bias-2")


def test_truncation_warning_is_a_bounded_heuristic() -> None:
    assert not bias_may_be_truncated("p" * BIAS_SOFT_LIMIT * 2, ())
    assert not bias_may_be_truncated(None, TERMS)
    assert bias_may_be_truncated(None, [str(i) * 100 for i in range(5)])
    assert not bias_may_be_truncated(None, ["t" * 100] * 5)  # duplicates count once
    assert bias_may_be_truncated("p" * BIAS_SOFT_LIMIT, ["term"])


# ----------------------------------------------------------------- B2 adapter


def test_adapter_compiles_the_glossary_and_keeps_hashes_without_one(
    settings_data: dict[str, Any],
) -> None:
    plain = ProcessingSettings.model_validate(settings_data)
    old = adapt_settings(plain, context()).config
    new = adapt_settings(plain, context(prompt_compiler=CURRENT_PROMPT_COMPILER)).config
    # No glossary: byte-identical core Config, so resume caches of existing jobs hold.
    assert old == new
    assert stage_config_hashes(old, "0" * 64) == stage_config_hashes(new, "0" * 64)

    data = {**settings_data, "asr": {**settings_data["asr"], "glossary": TERMS}}
    data["asr"]["initial_prompt"] = "prompt"
    biased = adapt_settings(
        ProcessingSettings.model_validate(data), context(prompt_compiler=CURRENT_PROMPT_COMPILER)
    ).config
    assert biased.asr.initial_prompt == "Utteran、話者分離、CTranslate2\nprompt"
    assert stage_config_hashes(biased, "0" * 64)["asr"] != stage_config_hashes(new, "0" * 64)["asr"]
    assert (
        stage_config_hashes(biased, "0" * 64)["audio"]
        == (stage_config_hashes(new, "0" * 64)["audio"])
    )


def test_adapter_refuses_glossary_for_the_old_compiler_and_unknown_versions(
    settings_data: dict[str, Any],
) -> None:
    data = {**settings_data, "asr": {**settings_data["asr"], "glossary": TERMS}}
    settings = ProcessingSettings.model_validate(data)
    with pytest.raises(ConfigAdaptationError) as error:
        adapt_settings(settings, context())
    assert (error.value.path, error.value.code) == ("/asr/glossary", "unsupported_setting")
    with pytest.raises(ConfigAdaptationError) as error:
        adapt_settings(settings, context(prompt_compiler="glossary-bias-2"))
    assert error.value.code == "invalid_execution_context"


# ------------------------------------------------- B7 snapshot -> IPC -> B3 Config


def _glossary(settings: dict[str, Any]) -> None:
    settings["asr"]["glossary"] = TERMS


def test_snapshot_freezes_glossary_and_compiler_and_b3_receives_the_compiled_prompt(
    tmp_path: Path, offline: list[Any]
) -> None:
    env = Env(tmp_path, _glossary)
    model_files(env.model)
    snapshot, request = planned(env)
    assert snapshot.resolved.settings.asr.glossary == tuple(TERMS)
    assert snapshot.resolved.prompt_compiler_version == PROMPT_COMPILER_VERSION
    assert request.context.prompt_compiler == CURRENT_PROMPT_COMPILER
    config = explicit_config(request)
    language = snapshot.resolved.settings.asr.language
    expected = compile_prompt(SENTINEL, TERMS, language, compiler=CURRENT_PROMPT_COMPILER)
    assert config.asr.initial_prompt == expected
    assert expected is not None and expected.endswith("\n" + SENTINEL)


def test_editing_the_preset_later_does_not_change_a_submitted_job(tmp_path: Path) -> None:
    env = Env(tmp_path, _glossary)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    before = env.store.get_snapshot(job.id).to_wire()
    repo = Repository(env.settings_path)
    current = repo.get("presets", env.preset_ref["id"])
    data = current.to_wire()
    data["settings"]["asr"].update(glossary=["changed"], initial_prompt="x")
    repo.mutate(
        "presets",
        current.id,
        1,
        PresetWrite.model_validate({k: data[k] for k in ("schema_version", "name", "settings")}),
    )
    assert repo.get("presets", current.id).revision == 2
    assert env.store.get_snapshot(job.id).to_wire() == before


def _plan(snapshot: JobSnapshot, tmp: Path) -> WorkerRequest:
    return plan_request(
        snapshot, job_id="job_" + "0" * 32, attempt_id="att_" + "1" * 32, job_dir=tmp / "jobs"
    )


def test_a_snapshot_frozen_before_glossary_support_keeps_its_meaning(
    tmp_path: Path, offline: list[Any]
) -> None:
    """Retry of a pre-0.1.53 snapshot: verbatim prompt, and never a glossary."""
    env = Env(tmp_path, None)
    model_files(env.model)
    snapshot, current = planned(env)
    data = snapshot.to_wire()
    data["resolved"]["prompt_compiler_version"] = "b2-verbatim"
    old = JobSnapshot.model_validate(data)
    request = over_ipc(_plan(old, tmp_path))
    assert request.context.prompt_compiler == "b2-verbatim"
    assert explicit_config(request) == explicit_config(current)
    assert explicit_config(request).asr.initial_prompt == SENTINEL
    # The same old snapshot with terms cannot have existed; if one appears it is refused.
    data["resolved"]["settings"]["asr"]["glossary"] = TERMS
    with pytest.raises(ProtocolError) as error:
        explicit_config(over_ipc(_plan(JobSnapshot.model_validate(data), tmp_path)))
    assert error.value.code is WorkerErrorCode.UNSUPPORTED_SETTING


def test_unknown_compiler_version_is_refused_by_the_private_schema(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    _, request = planned(env)
    data = request.model_dump(mode="json")
    data["context"]["prompt_compiler"] = "glossary-bias-2"
    with pytest.raises(ValueError):
        WorkerRequest.model_validate(data)


def test_glossary_on_an_engine_without_support_is_still_refused(tmp_path: Path) -> None:
    def updates(settings: dict[str, Any]) -> None:
        settings["asr"].pop("faster_whisper", None)
        settings["asr"].update(backend="whisper-cpp", glossary=TERMS)

    env = Env(tmp_path, updates)
    with pytest.raises(AdmissionError) as error:
        env.resolution(env.session(env.file("a.wav")))
    assert error.value.status == 422
    assert env.provider.calls == 0


# ------------------------------------------------------------- GET /api/v1/engines


def test_engine_catalog_route_is_static_guarded_and_value_free(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from test_b7_api import AUTHORITY, ROOT, runtime_for
    from test_b7_jobs import SESSION
    from utteran_contracts.engines import EngineCatalog, engine_catalog
    from utteran_gui.api import SESSION_HEADER, create_app
    from utteran_gui.operation_queue import OperationQueue

    env = Env(tmp_path, None)
    queue = OperationQueue()
    app = create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime_for(env, queue),
    )
    base = "http://" + AUTHORITY
    with TestClient(app, base_url=base, headers={SESSION_HEADER: SESSION}) as client:
        response = client.get("/api/v1/engines")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert EngineCatalog.model_validate(response.json()) == engine_catalog()
        body = response.text
        assert SENTINEL not in body and str(tmp_path) not in body
        # Same session / Host guards as every other v1 route.
        assert client.get("/api/v1/engines", headers={SESSION_HEADER: "wrong"}).status_code == 401
        assert client.get("/api/v1/engines", headers={"Host": "evil.example:8765"}).status_code == (
            403
        )
        assert client.post("/api/v1/engines", json={}).status_code == 405
    # The default (legacy) app has no such route.
    with TestClient(create_app(SESSION, repo_root=ROOT), base_url=base) as legacy:
        assert legacy.get("/api/v1/engines", headers={SESSION_HEADER: SESSION}).status_code == 404
