"""Pure leaf replacement and preview; never executes or resolves auto values."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import ValidationError

from utteran_contracts.editing import EditingSession, SettingOverride, leaf_adapters
from utteran_contracts.preview import Provenance, ValidatedFile, ValidationResult
from utteran_contracts.settings import Issue, Policy, Preset, ProcessingSettings
from utteran_settings.errors import SettingsError

_NAMESPACES = {
    "faster-whisper": "faster_whisper",
    "whisper-cpp": "whisper_cpp",
    "openvino-genai": "openvino_genai",
}
_NON_SPACE = {"ja", "zh", "yue", "th", "lo", "my", "auto", ""}
# These aliases belong to typed fields even when that field uses a default.
_TYPED = {
    "-bs",
    "--beam-size",
    "-l",
    "--language",
    "--prompt",
    "--initial-prompt",
    "-t",
    "--threads",
    "-tp",
    "--temperature",
    "-tpi",
    "--temperature-inc",
    "-et",
    "--entropy-thold",
    "-lpt",
    "--logprob-thold",
    "-nth",
    "--no-speech-thold",
    "-nc",
    "--no-context",
    "-dtw",
    "--dtw",
    "--vad",
    "-vt",
    "--vad-threshold",
    "--vad-model",
    "--max-len",
    "--word-timestamps",
}


def issue(path: str, code: str) -> Issue:
    return Issue(path=path, code=code, message_key=f"settings.{code}")


def safe_contract_issues(error: ValidationError, prefix: str = "") -> tuple[Issue, ...]:
    # Never echo map keys, unknown user field names, ctx, input, or exception text.
    known = {part for path in leaf_adapters() for part in path.split("/")}
    known.update(
        {
            "schema_version",
            "name",
            "settings",
            "files",
            "overrides",
            "path",
            "value",
            "common_overrides",
            "preset_ref",
            "policy_ref",
            "id",
            "revision",
        }
    )
    result: list[Issue] = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        parts = [
            str(p) if isinstance(p, int) else p if p in known else "field" for p in item["loc"]
        ]
        result.append(
            issue(prefix + ("/" + "/".join(parts) if parts else ""), "contract_validation_failed")
        )
    return tuple(result)


def semantic_issues(settings: ProcessingSettings, overrides: set[str]) -> tuple[Issue, ...]:
    asr = settings.asr
    result: list[Issue] = []
    if asr.backend != "auto":
        active = _NAMESPACES[asr.backend]
        for namespace in _NAMESPACES.values():
            if namespace != active and getattr(asr, namespace) is not None:
                paths = sorted(p for p in overrides if p.startswith(f"/asr/{namespace}/"))
                result.extend(issue(p, "incompatible_override") for p in paths)
                if not paths:
                    result.append(issue(f"/asr/{namespace}", "inactive_backend_settings"))
    native = asr.whisper_cpp
    if asr.backend == "whisper-cpp" and native is not None:
        if native.vad is not None and native.vad != asr.vad.enabled:
            result.append(issue("/asr/whisper_cpp/vad", "context_conflict"))
        if (
            native.vad_threshold is not None
            and asr.vad.threshold is not None
            and native.vad_threshold != asr.vad.threshold
        ):
            result.append(issue("/asr/whisper_cpp/vad_threshold", "context_conflict"))
    if settings.diarization.enabled and asr.word_timestamps == "never":
        result.append(issue("/asr/word_timestamps", "diarization_requires_timestamps"))
    if asr.backend == "openvino-genai" and asr.language.strip().casefold() in _NON_SPACE:
        if settings.diarization.enabled:
            result.append(issue("/diarization/enabled", "genai_diarization_language"))
        if asr.word_timestamps == "always":
            result.append(issue("/asr/word_timestamps", "genai_word_timestamps_language"))
    extra = asr.extra_arguments
    if extra is not None:
        if asr.backend not in {"auto", extra.backend}:
            result.append(issue("/asr/extra_arguments/backend", "incompatible_override"))
        if extra.tokens:
            # No native-version option registry is shipped in B5. Unknown tokens
            # cannot be proved safe even with Expert consent, so fail closed.
            conflict = any(t.split("=", 1)[0] in _TYPED for t in extra.tokens)
            result.append(
                issue(
                    "/asr/extra_arguments/tokens",
                    "argument_conflict" if conflict else "argument_unverified",
                )
            )
    return tuple(result)


def _get(data: dict[str, Any], path: str) -> tuple[Any, bool]:
    value: Any = data
    for part in path.strip("/").split("/"):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _apply(
    data: dict[str, Any],
    overrides: tuple[SettingOverride, ...],
    sources: dict[str, Literal["preset", "common", "file"]],
    source: Literal["common", "file"],
) -> None:
    for override in overrides:
        parts = override.path.strip("/").split("/")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = override.to_wire()["value"]
        sources[override.path] = source


def preview(session: EditingSession, preset: Preset, policy: Policy) -> ValidationResult:
    if session.preset_ref.id != preset.id or session.preset_ref.revision != preset.revision:
        raise SettingsError(
            "reference_unresolved", 422, (issue("/preset_ref", "reference_unresolved"),)
        )
    if session.policy_ref.id != policy.id or session.policy_ref.revision != policy.revision:
        raise SettingsError(
            "reference_unresolved", 422, (issue("/policy_ref", "reference_unresolved"),)
        )
    common = deepcopy(preset.settings.to_wire())
    common_sources: dict[str, Literal["preset", "common", "file"]] = {
        path: "preset" for path in leaf_adapters()
    }
    _apply(common, session.common_overrides, common_sources, "common")
    files = []
    for file in session.files:
        data = deepcopy(common)
        sources = common_sources.copy()
        _apply(data, file.overrides, sources, "file")
        provenance = []
        for path in sorted(leaf_adapters()):
            value, present = _get(data, path)
            status: Literal["value", "null", "empty", "absent"] = (
                "absent"
                if not present
                else "null"
                if value is None
                else "empty"
                if value in ("", [], {})
                else "value"
            )
            normalized = value
            if path == "/asr/initial_prompt" and value == "":
                normalized = None
            if path == "/asr/language" and isinstance(value, str):
                normalized = "auto" if value.strip().casefold() == "auto" else value.strip()
            normalize = normalized != value
            provenance.append(
                Provenance(
                    path=path,
                    source="normalization" if normalize else sources[path],
                    input_source=sources[path],
                    input_status=status,
                )
            )
            if normalize:
                data["asr"][path.rsplit("/", 1)[1]] = normalized
        try:
            effective = ProcessingSettings.model_validate(data)
            paths = {o.path for o in (*session.common_overrides, *file.overrides)}
            issues = semantic_issues(effective, paths)
        except ValidationError as exc:
            effective = None
            issues = safe_contract_issues(exc)
        warnings = []
        unsupported = False
        if effective is not None:
            asr = effective.asr
            for path, condition in (
                ("/asr/backend", asr.backend not in {"auto", "faster-whisper"}),
                ("/diarization/enabled", effective.diarization.enabled),
                ("/asr/glossary", bool(asr.glossary)),
                ("/asr/extra_arguments", asr.extra_arguments is not None),
                ("/asr/word_timestamps", asr.word_timestamps == "never"),
                ("/asr/vad/threshold", asr.vad.threshold is not None),
                (
                    "/postprocessing/fallback_characters_per_second",
                    effective.postprocessing.fallback_characters_per_second == 0,
                ),
                (
                    "/postprocessing/fallback_min_duration",
                    effective.postprocessing.fallback_min_duration == 0,
                ),
            ):
                if condition:
                    unsupported = True
                    warnings.append(issue(path, "worker_unsupported"))
            if asr.glossary or asr.initial_prompt:
                warnings.append(issue("/asr/initial_prompt", "prompt_quality_unverified"))
        files.append(
            ValidatedFile(
                client_file_id=file.client_file_id,
                valid=not issues,
                effective_settings=effective,
                provenance=tuple(provenance),
                issues=issues,
                warnings=tuple(warnings),
                worker_support="unsupported" if unsupported else "unverified",
            )
        )
    return ValidationResult(
        schema_version=1,
        valid=all(f.valid for f in files),
        preset_ref=session.preset_ref,
        policy_ref=session.policy_ref,
        files=tuple(files),
    )
