import importlib
import os
from pathlib import Path

import pytest

from tracequant.config import (
    ENV_VAR_NAMES,
    ConfigError,
    Environment,
    LogFormat,
    LogLevel,
    SecretValue,
    Settings,
    load_settings,
)


def test_load_settings_uses_environment_and_defaults() -> None:
    settings = load_settings(environ={"TRACEQUANT_ENV": "development"})

    assert settings == Settings(
        environment=Environment.DEVELOPMENT,
        log_level=LogLevel.INFO,
        log_format=LogFormat.JSON,
        log_dir=None,
    )


def test_load_settings_supports_all_configured_environment_variables() -> None:
    settings = load_settings(
        environ={
            "TRACEQUANT_ENV": "production",
            "TRACEQUANT_LOG_LEVEL": "warning",
            "TRACEQUANT_LOG_FORMAT": "json",
            "TRACEQUANT_LOG_DIR": "logs/app",
        }
    )

    assert settings.environment is Environment.PRODUCTION
    assert settings.log_level is LogLevel.WARNING
    assert settings.log_format is LogFormat.JSON
    assert settings.log_dir == Path("logs/app")


def test_explicit_arguments_take_precedence_over_process_environment() -> None:
    settings = load_settings(
        environment=Environment.TEST,
        log_level=LogLevel.ERROR,
        log_format=LogFormat.JSON,
        log_dir=Path("explicit-logs"),
        environ={
            "TRACEQUANT_ENV": "production",
            "TRACEQUANT_LOG_LEVEL": "DEBUG",
            "TRACEQUANT_LOG_FORMAT": "text",
            "TRACEQUANT_LOG_DIR": "env-logs",
        },
    )

    assert settings == Settings(
        environment=Environment.TEST,
        log_level=LogLevel.ERROR,
        log_format=LogFormat.JSON,
        log_dir=Path("explicit-logs"),
    )


def test_load_settings_rejects_missing_required_environment() -> None:
    with pytest.raises(ConfigError, match="^TRACEQUANT_ENV is required$"):
        load_settings(environ={})


def test_load_settings_rejects_empty_required_environment() -> None:
    with pytest.raises(ConfigError, match="^TRACEQUANT_ENV must not be empty$"):
        load_settings(environ={"TRACEQUANT_ENV": ""})


@pytest.mark.parametrize("value", ["staging", "prod", ""])
def test_load_settings_rejects_invalid_environment(value: str) -> None:
    with pytest.raises(ConfigError, match="TRACEQUANT_ENV"):
        load_settings(environ={"TRACEQUANT_ENV": value})


def test_load_settings_rejects_invalid_log_level() -> None:
    with pytest.raises(ConfigError, match="TRACEQUANT_LOG_LEVEL"):
        load_settings(
            environ={
                "TRACEQUANT_ENV": "test",
                "TRACEQUANT_LOG_LEVEL": "verbose",
            }
        )


def test_load_settings_rejects_invalid_log_format() -> None:
    with pytest.raises(ConfigError, match="TRACEQUANT_LOG_FORMAT"):
        load_settings(
            environ={
                "TRACEQUANT_ENV": "test",
                "TRACEQUANT_LOG_FORMAT": "xml",
            }
        )


def test_log_dir_empty_string_disables_file_logging() -> None:
    settings = load_settings(
        environ={
            "TRACEQUANT_ENV": "test",
            "TRACEQUANT_LOG_DIR": "",
        }
    )

    assert settings.log_dir is None


@pytest.mark.parametrize("value", ["logs\\app", "logs/app", "/tmp/tracequant-logs"])
def test_log_dir_uses_pathlib_without_creating_directory(value: str) -> None:
    settings = load_settings(
        environ={
            "TRACEQUANT_ENV": "test",
            "TRACEQUANT_LOG_DIR": value,
        }
    )

    assert settings.log_dir == Path(value)
    assert not Path(value).exists()


def test_settings_are_immutable() -> None:
    settings = load_settings(environ={"TRACEQUANT_ENV": "test"})

    with pytest.raises(AttributeError):
        settings.environment = Environment.PRODUCTION  # type: ignore[misc]


def test_unknown_tracequant_environment_variables_are_ignored() -> None:
    settings = load_settings(
        environ={
            "TRACEQUANT_ENV": "test",
            "TRACEQUANT_UNKNOWN": "ignored",
        }
    )

    assert settings.environment is Environment.TEST


def test_config_module_import_does_not_read_environment_or_create_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRACEQUANT_ENV", "production")
    monkeypatch.chdir(tmp_path)

    module = importlib.reload(importlib.import_module("tracequant.config"))

    assert "TRACEQUANT_ENV" in os.environ
    assert module.ENV_VAR_NAMES == ENV_VAR_NAMES
    assert list(tmp_path.iterdir()) == []


def test_secret_value_repr_and_str_are_redacted() -> None:
    secret = SecretValue("sensitive-value-\u79d8\u5bc6")

    assert repr(secret) == "SecretValue(<redacted>)"
    assert str(secret) == "<redacted>"
    assert "sensitive-value" not in f"{secret!r} {secret}"
    assert secret.reveal() == "sensitive-value-\u79d8\u5bc6"


def test_secret_value_rejects_empty_string_without_leaking_value() -> None:
    with pytest.raises(ValueError, match="^secret value must not be empty$"):
        SecretValue("")


def test_environment_variable_names_are_centralized() -> None:
    assert ENV_VAR_NAMES == (
        "TRACEQUANT_ENV",
        "TRACEQUANT_LOG_LEVEL",
        "TRACEQUANT_LOG_FORMAT",
        "TRACEQUANT_LOG_DIR",
    )
