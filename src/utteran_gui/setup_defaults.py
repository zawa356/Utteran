"""束F (PR #91 P2-1): server-side "first setup → common defaults" for one setup plan.

The intent (``apply_defaults``) and the verified settings live in the hashed, stored plan;
the creation is one B5 transaction (create-if-empty) keyed by the plan id, so:

- closing the window, a lost answer or a second window all converge on the same pair;
- a partial pair cannot exist (both resources or none);
- an existing Preset or Policy (made anywhere, any time) means "kept": nothing is changed;
- a re-verification plan never creates defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utteran_contracts.settings import PolicyWrite, PresetWrite
from utteran_contracts.setup import SETUP_MODELS
from utteran_control.setup_repository import SetupControlStore
from utteran_settings.repository import Repository


class DefaultsRefused(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _key(plan_id: str) -> str:
    return "setup-defaults-" + plan_id.removeprefix("plan_")


def _plan(store: SetupControlStore, operation_id: str) -> tuple[Any, Any]:
    operation = store.get_setup(operation_id)
    content = store.get_plan_content(operation.plan_ref.id)
    return operation, content


def defaults_view(
    store: SetupControlStore, settings_path: Path, operation_id: str
) -> dict[str, Any]:
    """GET: read-only. ``pending`` = intended, not yet applied; ``not_applicable`` otherwise."""
    operation, content = _plan(store, operation_id)
    if not content.apply_defaults or content.verification is None:
        return {"schema_version": 1, "state": "not_applicable", "preset": None, "policy": None}
    if not settings_path.is_file():
        recorded = None  # GET never creates the settings store
    else:
        recorded = Repository(settings_path).defaults_state(_key(operation.plan_ref.id))
    if recorded is None:
        state = "pending" if operation.status == "succeeded" else "waiting"
        return {"schema_version": 1, "state": state, "preset": None, "policy": None}
    if recorded.get("state") not in {"created", "kept"}:
        from utteran_settings.errors import SettingsError

        raise SettingsError("store_corrupt")
    return {
        "schema_version": 1,
        "state": recorded["state"],
        "preset": recorded.get("preset"),
        "policy": recorded.get("policy"),
    }


def apply_defaults(
    store: SetupControlStore, settings_path: Path, operation_id: str
) -> dict[str, Any]:
    """POST: only after this operation succeeded, only for a plan that asked for it."""
    operation, content = _plan(store, operation_id)
    verification = content.verification
    if not content.apply_defaults or verification is None:
        raise DefaultsRefused("defaults_not_requested")
    if operation.status != "succeeded":
        raise DefaultsRefused("setup_not_succeeded")
    model = SETUP_MODELS[verification.model_ref].model_id
    preset = PresetWrite.model_validate(
        {"schema_version": 1, "name": f"Setup {model}", "settings": verification.settings.to_wire()}
    )
    policy = PolicyWrite.model_validate(
        {
            "schema_version": 1,
            "name": f"Setup {content.profile_kind}",
            "mode": "auto",
            "fixed_candidate_id": None,
            "allowed_profile_ids": [content.profile_kind],
            "objective": "balanced",
            "max_memory_bytes": None,
            "fallback": "none",
            "execution_deadline_seconds": None,
        }
    )
    result = Repository(settings_path).create_defaults_if_empty(
        _key(operation.plan_ref.id), preset, policy
    )
    return {
        "schema_version": 1,
        "state": result["state"],
        "preset": result["preset"],
        "policy": result["policy"],
    }
