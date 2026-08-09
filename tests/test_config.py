from __future__ import annotations

from pathlib import Path

import pytest

from utteran.config import (
    Config,
    DotEnvTokenProvider,
    default_token_provider,
    initialize_config,
)
from utteran.errors import ConfigurationError
from utteran.jobs import stage_config_hashes


def test_whisper_cpp_defaults() -> None:
    config = Config()

    assert config.asr.word_timestamps == "auto"
    assert config.asr.whisper_cpp.variant == "auto"
    assert config.asr.whisper_cpp.dtw == "auto"
    assert config.asr.whisper_cpp.no_context is True
    assert config.asr.whisper_cpp.repetition_limit == 10


def test_qualified_asr_model_uses_same_canonical_id_and_hash() -> None:
    unqualified = Config.model_validate({"asr": {"backend": "faster-whisper", "model": "large-v3"}})
    qualified = Config.model_validate(
        {
            "asr": {
                "backend": "faster-whisper",
                "model": "faster-whisper:large-v3",
            }
        }
    )

    assert qualified.asr.model == "large-v3"
    assert (
        stage_config_hashes(qualified, "input-hash")["asr"]
        == (stage_config_hashes(unqualified, "input-hash")["asr"])
    )


def test_config_priority_cli_env_dotenv_toml_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[asr]\nbeam_size = 2\nlanguage = 'de'\nmodel = 'small'\n",
        encoding="utf-8",
    )
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "UTTERAN_ASR__BEAM_SIZE=3\n"
        "UTTERAN_ASR__LANGUAGE=fr\n"
        "UTTERAN_ASR__CONDITION_ON_PREVIOUS_TEXT=false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UTTERAN_ASR__BEAM_SIZE", "4")
    monkeypatch.setenv("UTTERAN_ASR__CONDITION_ON_PREVIOUS_TEXT", "true")

    config = Config.load(
        config_path=config_path,
        dotenv_path=dotenv_path,
        cli_overrides={"asr": {"beam_size": 5}},
    )

    assert config.asr.beam_size == 5
    assert config.asr.language == "fr"
    assert config.asr.condition_on_previous_text is True
    assert config.asr.model == "small"
    assert config.asr.device == "auto"


def test_token_in_toml_is_ignored_and_warned(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("hf_token = 'hf_neverlogthis'\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="安全のため無視"):
        config = Config.load(config_path=config_path, dotenv_path=tmp_path / "missing")

    assert "hf_token" not in config.model_dump()


def test_dotenv_token_provider(tmp_path: Path) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("HF_TOKEN=hf_testsecret\n", encoding="utf-8")

    assert DotEnvTokenProvider(dotenv_path).get_token() == "hf_testsecret"


def test_token_priority_environment_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("HF_TOKEN=hf_from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN", "hf_from_environment")

    assert default_token_provider(dotenv_path).get_token() == "hf_from_environment"


def test_documented_empty_path_sentinels_use_platform_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[general]\njob_dir = ''\n[ffmpeg]\npath = ''\n",
        encoding="utf-8",
    )

    config = Config.load(config_path=config_path, dotenv_path=tmp_path / "missing")

    assert config.general.job_dir is None
    assert config.ffmpeg.path is None


def test_venv_native_dir_and_default_profile_empty_sentinels(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[general]\nvenv_dir = ''\nnative_dir = ''\ndefault_profile = ''\n",
        encoding="utf-8",
    )

    config = Config.load(config_path=config_path, dotenv_path=tmp_path / "missing")

    assert config.general.venv_dir is None
    assert config.general.native_dir is None
    assert config.general.default_profile is None


def test_venv_native_dir_and_default_profile_explicit_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[general]\nvenv_dir = 'C:/venvs'\nnative_dir = 'C:/native'\ndefault_profile = 'cuda'\n",
        encoding="utf-8",
    )

    config = Config.load(config_path=config_path, dotenv_path=tmp_path / "missing")

    assert config.general.venv_dir == Path("C:/venvs")
    assert config.general.native_dir == Path("C:/native")
    assert config.general.default_profile == "cuda"


def test_config_init_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    assert initialize_config(path) == path
    original = path.read_text(encoding="utf-8")
    assert "HF_TOKEN" not in original
    with pytest.raises(ConfigurationError, match="既に存在"):
        initialize_config(path)
    assert path.read_text(encoding="utf-8") == original
