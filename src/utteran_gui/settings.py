"""GUI-only settings and OS-keyring token storage."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from platformdirs import user_config_dir

from utteran_gui.security import register_secret

Theme = Literal["dark", "light"]
Language = Literal["ja", "en"]
PROFILE_NAMES = ("cpu", "cuda", "intel", "vulkan")


@dataclass(frozen=True)
class GuiSettings:
    """Non-sensitive preferences persisted separately from CLI config and jobs."""

    theme: Theme = "dark"
    language: Language = "ja"
    default_profile: str | None = None
    default_input_dir: str = ""
    default_output_dir: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> GuiSettings:
        """Restore known fields while safely ignoring corrupt or future data."""
        if not isinstance(payload, dict):
            return cls()
        theme = payload.get("theme")
        language = payload.get("language")
        profile = payload.get("default_profile")
        return cls(
            theme=cast(Theme, theme if theme in {"dark", "light"} else "dark"),
            language=cast(Language, language if language in {"ja", "en"} else "ja"),
            default_profile=(str(profile) if profile in PROFILE_NAMES else None),
            default_input_dir=_bounded_string(payload.get("default_input_dir")),
            default_output_dir=_bounded_string(payload.get("default_output_dir")),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SettingsStore:
    """Atomic platformdirs-backed GUI preference store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(user_config_dir("utteran-gui")) / "settings.json"

    def load(self) -> GuiSettings:
        try:
            with self.path.open(encoding="utf-8") as file:
                return GuiSettings.from_dict(json.load(file))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return GuiSettings()

    def save(self, settings: GuiSettings) -> GuiSettings:
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


class KeyringLike(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class TokenStore:
    """Store the Hugging Face token only in the same OS keyring slot used by CLI."""

    SERVICE = "utteran"
    USERNAME = "huggingface"

    def __init__(self, backend: KeyringLike | None = None) -> None:
        self._backend = backend

    def is_configured(self) -> bool:
        token = self._get_token()
        register_secret(token)
        return bool(token)

    def set(self, token: str) -> None:
        selected = token.strip()
        if not selected:
            raise ValueError("token must not be empty")
        register_secret(selected)
        self._keyring().set_password(self.SERVICE, self.USERNAME, selected)

    def clear(self) -> None:
        try:
            self._keyring().delete_password(self.SERVICE, self.USERNAME)
        except Exception:
            if self._get_token() is not None:
                raise

    def _get_token(self) -> str | None:
        try:
            return self._keyring().get_password(self.SERVICE, self.USERNAME)
        except Exception:
            return None

    def _keyring(self) -> KeyringLike:
        if self._backend is None:
            import keyring

            self._backend = cast(KeyringLike, keyring)
        return self._backend


def _bounded_string(value: object) -> str:
    return str(value)[:4096] if isinstance(value, str) else ""
