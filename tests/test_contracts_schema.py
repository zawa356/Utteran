from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from utteran_contracts.schema import MODELS, contract_schema, main, schema_bytes

ROOT = Path(__file__).parents[1]


def normalize(schema: Any) -> Any:
    """Compare wire shape/constraints, ignoring schema annotation and ref spelling."""
    if isinstance(schema, list):
        return [normalize(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in {"title", "description", "default"}:
            continue
        if key == "$ref":
            result[key] = value.rsplit("/", 1)[-1]
        elif key == "required":
            if value:
                result[key] = sorted(value)
        else:
            result[key] = normalize(value)
    return result


def test_phase_a_wire_shape_matches_implemented_subset() -> None:
    proposed = yaml.safe_load(
        (ROOT / "docs/architecture/phase-a/openapi.yaml").read_text(encoding="utf-8")
    )
    source = proposed["components"]["schemas"]
    generated = contract_schema()["$defs"]
    for model in MODELS:
        name = model.__name__
        if name == "SettingOverride":
            # Deliberate tightening: registered leaf enum and JSON-only value.
            assert generated[name]["additionalProperties"] is False
            assert sorted(generated[name]["required"]) == ["path", "value"]
            continue
        actual, expected = copy.deepcopy(generated[name]), copy.deepcopy(source[name])
        if name in {"Policy", "PolicyWrite"}:
            # These two schemas intentionally add the prose-only auto-policy rule.
            # Compare their conditional semantics separately, never globally drop allOf.
            actual.pop("allOf")
            expected.pop("allOf")
        assert normalize(actual) == normalize(expected), name


def test_schema_valid_examples_and_negative_inputs() -> None:
    schema = contract_schema()
    Draft202012Validator.check_schema(schema)
    examples = json.loads(
        (ROOT / "docs/architecture/phase-a/examples.json").read_text(encoding="utf-8")
    )
    for key, name in [
        ("preset", "PresetWrite"),
        ("policy", "PolicyWrite"),
        ("editing", "EditingSession"),
        ("error", "Error"),
    ]:
        target = {**schema, "$ref": f"#/$defs/{name}"}
        target.pop("oneOf")
        Draft202012Validator(target).validate(examples[key])
    for mutate in (
        lambda x: x.update(schema_version=2),
        lambda x: x.update(unknown=True),
        lambda x: x["settings"]["asr"].update(beam_size=0),
        lambda x: x["settings"]["asr"].update(whisper_cpp=None),
    ):
        data = copy.deepcopy(examples["preset"])
        mutate(data)
        assert list(
            Draft202012Validator({**schema, "$ref": "#/$defs/PresetWrite"}).iter_errors(data)
        )
    policy = {**examples["policy"], "fallback": "preapproved"}
    assert list(Draft202012Validator({**schema, "$ref": "#/$defs/PolicyWrite"}).iter_errors(policy))


def test_schema_reproducibility_and_check_mode(tmp_path: Path, capsys: Any) -> None:
    output = tmp_path / "schema.json"
    content = schema_bytes()
    assert content == schema_bytes()
    assert content == (ROOT / "docs/architecture/b1/contracts.schema.json").read_bytes()
    assert main(["--output", str(output)]) == 0
    before = output.stat().st_mtime_ns
    assert main(["--output", str(output), "--check"]) == 0
    assert output.stat().st_mtime_ns == before
    output.write_text("{}", encoding="utf-8")
    assert main(["--output", str(output), "--check"]) == 1
    assert output.read_text(encoding="utf-8") == "{}"
    assert "stale" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--check"])


def test_override_leaf_constraints_are_exported() -> None:
    schema = contract_schema()
    schema.pop("oneOf")
    target = Draft202012Validator({**schema, "$ref": "#/$defs/SettingOverride"})
    for data in (
        {"path": "/asr/beam_size", "value": None},
        {"path": "/asr/beam_size", "value": "5"},
        {"path": "/asr/whisper_cpp/threads", "value": None},
        {"path": "/output/formats", "value": ["srt", "srt"]},
        {"path": "/asr", "value": {}},
        {"path": "/asr/word_timestamps", "value": "invalid"},
    ):
        assert list(target.iter_errors(data))
    target.validate({"path": "/asr/initial_prompt", "value": None})
    target.validate({"path": "/diarization/enabled", "value": False})


@pytest.mark.parametrize(
    "mode,candidate,fallback,expected",
    [
        ("fixed", None, "none", False),
        ("fixed", "synthetic", "preapproved", False),
        ("fixed", "synthetic", "none", True),
        ("auto", None, "preapproved", True),
    ],
)
def test_phase_a_allof_policy_rules_are_preserved(
    mode: str, candidate: Any, fallback: str, expected: bool
) -> None:
    source = yaml.safe_load(
        (ROOT / "docs/architecture/phase-a/openapi.yaml").read_text(encoding="utf-8")
    )["components"]["schemas"]
    payload = {"mode": mode, "fixed_candidate_id": candidate, "fallback": fallback}
    for name in ("Policy", "PolicyWrite"):
        actual = contract_schema()["$defs"][name]
        for schema in (source[name], actual):
            assert (
                not list(Draft202012Validator({"allOf": schema["allOf"]}).iter_errors(payload))
            ) is expected
    # The B1-only auto restriction is explicit and tested, not normalized away.
    wrong_auto = {**payload, "mode": "auto", "fixed_candidate_id": "synthetic"}
    assert list(Draft202012Validator({"allOf": actual["allOf"]}).iter_errors(wrong_auto))
