"""GUI-only settings and OS-keyring token storage."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from platformdirs import user_config_dir

from utteran_gui.security import mask_secrets, register_secret

Theme = Literal["system", "dark", "light"]
Language = Literal["ja", "en"]
PROFILE_NAMES = ("cpu", "cuda", "intel", "vulkan")
WIZARD_STEPS = (
    "welcome",
    "profile",
    "diarization",
    "token",
    "model",
    "confirm",
    "execution",
    "done",
)
WIZARD_EXECUTION_STAGES = ("venv", "preflight", "diarization_model", "asr_model", "smoke")


@dataclass(frozen=True)
class GuiSettings:
    """Non-sensitive preferences persisted separately from CLI config and jobs."""

    theme: Theme = "system"
    language: Language = "ja"
    default_profile: str | None = None
    default_input_dir: str = ""
    default_output_dir: str = ""
    setup_wizard_started_at: str | None = None
    setup_wizard_completed_at: str | None = None
    setup_wizard_step: str = "welcome"
    setup_wizard_profile: str | None = None
    setup_wizard_diarization_enabled: bool | None = None
    setup_wizard_model_ref: str = "faster-whisper:large-v3-turbo"
    setup_wizard_completed_stages: tuple[str, ...] = ()
    setup_wizard_token_error: str | None = None

    @classmethod
    def from_dict(cls, payload: object) -> GuiSettings:
        """Restore known fields while safely ignoring corrupt or future data."""
        if not isinstance(payload, dict):
            return cls()
        theme = payload.get("theme")
        language = payload.get("language")
        profile = payload.get("default_profile")
        started_at = payload.get("setup_wizard_started_at")
        completed_at = payload.get("setup_wizard_completed_at")
        wizard_step = payload.get("setup_wizard_step")
        wizard_profile = payload.get("setup_wizard_profile")
        wizard_diarization = payload.get("setup_wizard_diarization_enabled")
        wizard_model = payload.get("setup_wizard_model_ref")
        raw_stages = payload.get("setup_wizard_completed_stages")
        token_error = payload.get("setup_wizard_token_error")
        completed_stages = (
            tuple(
                str(stage)
                for stage in raw_stages
                if isinstance(stage, str) and stage in WIZARD_EXECUTION_STAGES
            )
            if isinstance(raw_stages, (list, tuple))
            else ()
        )
        return cls(
            theme=cast(Theme, theme if theme in {"system", "dark", "light"} else "system"),
            language=cast(Language, language if language in {"ja", "en"} else "ja"),
            default_profile=(str(profile) if profile in PROFILE_NAMES else None),
            default_input_dir=_bounded_string(payload.get("default_input_dir")),
            default_output_dir=_bounded_string(payload.get("default_output_dir")),
            setup_wizard_started_at=(
                str(started_at) if isinstance(started_at, str) and started_at else None
            ),
            setup_wizard_completed_at=(
                str(completed_at) if isinstance(completed_at, str) and completed_at else None
            ),
            setup_wizard_step=(
                str(wizard_step) if wizard_step in WIZARD_STEPS else "welcome"
            ),
            setup_wizard_profile=(
                str(wizard_profile) if wizard_profile in PROFILE_NAMES else None
            ),
            setup_wizard_diarization_enabled=(
                wizard_diarization if isinstance(wizard_diarization, bool) else None
            ),
            setup_wizard_model_ref=(
                str(wizard_model)[:200]
                if isinstance(wizard_model, str) and wizard_model.strip()
                else "faster-whisper:large-v3-turbo"
            ),
            setup_wizard_completed_stages=tuple(dict.fromkeys(completed_stages)),
            setup_wizard_token_error=(
                str(token_error)
                if token_error
                in {
                    "token_missing",
                    "token_invalid",
                    "agreement_required",
                    "network_error",
                }
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SettingsStore:
    """Atomic platformdirs-backed GUI preference store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(user_config_dir("utteran-gui")) / "settings.json"
        self._lock = threading.RLock()

    def load(self) -> GuiSettings:
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as file:
                    return GuiSettings.from_dict(json.load(file))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return GuiSettings()

    def save(self, settings: GuiSettings) -> GuiSettings:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                    json.dump(settings.to_dict(), file, ensure_ascii=False, indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                temporary_path.replace(self.path)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
            return settings

    def update(self, changes: dict[str, object]) -> GuiSettings:
        """Atomically merge known fields so concurrent partial saves cannot roll back."""
        with self._lock:
            current = self.load().to_dict()
            current.update(changes)
            return self.save(GuiSettings.from_dict(current))


class KeyringLike(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class TokenStoreUnavailable(RuntimeError):
    """The configured OS credential store cannot be used."""


@dataclass(frozen=True)
class TokenStatus:
    """Non-secret state suitable for diagnostics and API responses."""

    configured: bool
    available: bool
    backend: str
    error_type: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class TokenStore:
    """Store the Hugging Face token only in the same OS keyring slot used by CLI."""

    SERVICE = "utteran"
    USERNAME = "huggingface"

    def __init__(self, backend: KeyringLike | None = None) -> None:
        self._backend = backend

    def is_configured(self) -> bool:
        status = self.status()
        if not status.available:
            raise TokenStoreUnavailable(status.error_message or "OS keyring is unavailable")
        return status.configured

    def status(self) -> TokenStatus:
        """Return configured/unavailable as distinct states without exposing a token."""
        backend_name = "unavailable"
        try:
            backend = self._keyring()
            backend_name = self._backend_name(backend)
            token = backend.get_password(self.SERVICE, self.USERNAME)
            register_secret(token)
            return TokenStatus(bool(token), True, backend_name)
        except Exception as exc:
            return TokenStatus(
                False,
                False,
                backend_name,
                type(exc).__name__,
                str(exc),
            )

    def set(self, token: str) -> None:
        selected = token.strip()
        if not selected:
            raise ValueError("token must not be empty")
        register_secret(selected)
        try:
            backend = self._keyring()
            backend.set_password(self.SERVICE, self.USERNAME, selected)
            restored = backend.get_password(self.SERVICE, self.USERNAME)
            register_secret(restored)
        except Exception as exc:
            raise TokenStoreUnavailable(str(exc)) from exc
        if restored != selected:
            raise TokenStoreUnavailable("OS keyring did not return the saved credential")

    def clear(self) -> None:
        try:
            self._keyring().delete_password(self.SERVICE, self.USERNAME)
        except Exception as exc:
            status = self.status()
            if not status.available or status.configured:
                raise TokenStoreUnavailable(str(exc)) from exc

    def diagnose(self) -> dict[str, object]:
        """Probe import/backend/get/set/delete using an isolated synthetic credential."""
        result: dict[str, object] = {
            "import_success": False,
            "backend": "unavailable",
            "get_success": False,
            "set_success": False,
            "delete_success": False,
            "error_type": "",
            "error_message": "",
        }
        diagnostic_username = f"{self.USERNAME}-diagnostic-{secrets.token_hex(8)}"
        diagnostic_token = f"hf_diagnostic_{secrets.token_hex(16)}"
        register_secret(diagnostic_token)
        backend: KeyringLike | None = None
        stored = False
        try:
            backend = self._keyring()
            result["import_success"] = True
            result["backend"] = self._backend_name(backend)
            existing = backend.get_password(self.SERVICE, self.USERNAME)
            register_secret(existing)
            result["get_success"] = True
            backend.set_password(self.SERVICE, diagnostic_username, diagnostic_token)
            stored = True
            restored = backend.get_password(self.SERVICE, diagnostic_username)
            register_secret(restored)
            if restored != diagnostic_token:
                raise TokenStoreUnavailable("OS keyring did not return the diagnostic credential")
            result["set_success"] = True
            backend.delete_password(self.SERVICE, diagnostic_username)
            stored = False
            result["delete_success"] = True
        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error_message"] = mask_secrets(str(exc))
        finally:
            if stored and backend is not None:
                with suppress(Exception):
                    backend.delete_password(self.SERVICE, diagnostic_username)
        return result

    def _keyring(self) -> KeyringLike:
        if self._backend is None:
            import keyring

            self._backend = cast(KeyringLike, keyring)
        return self._backend

    def _backend_name(self, backend: KeyringLike) -> str:
        selected = backend
        get_keyring = getattr(backend, "get_keyring", None)
        if callable(get_keyring):
            selected = cast(KeyringLike, get_keyring())
        selected_type = type(selected)
        return f"{selected_type.__module__}.{selected_type.__qualname__}"


def _bounded_string(value: object) -> str:
    return str(value)[:4096] if isinstance(value, str) else ""
