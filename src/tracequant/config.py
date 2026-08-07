"""Application configuration loading and secret-safe value helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

__all__ = [
    "ENV_VAR_NAMES",
    "ConfigError",
    "Environment",
    "LogFormat",
    "LogLevel",
    "SecretValue",
    "Settings",
    "load_settings",
]


class ConfigError(ValueError):
    """Raised when application configuration is missing or invalid."""


class Environment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported standard logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(StrEnum):
    """Supported log output formats."""

    TEXT = "text"
    JSON = "json"


class EnvVarName(StrEnum):
    """Centralized environment variable names read by the config loader."""

    ENVIRONMENT = "TRACEQUANT_ENV"
    LOG_LEVEL = "TRACEQUANT_LOG_LEVEL"
    LOG_FORMAT = "TRACEQUANT_LOG_FORMAT"
    LOG_DIR = "TRACEQUANT_LOG_DIR"


ENV_VAR_NAMES: Final[tuple[str, ...]] = tuple(name.value for name in EnvVarName)
_UNSET: Final = object()


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A secret string whose normal representation never exposes the value."""

    _value: str

    def __post_init__(self) -> None:
        if not isinstance(self._value, str):
            raise TypeError("secret value must be a string")
        if self._value == "":
            raise ValueError("secret value must not be empty")

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def reveal(self) -> str:
        """Return the raw secret for explicit integrations that need it."""
        return self._value


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings."""

    environment: Environment
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON
    log_dir: Path | None = None


def load_settings(
    *,
    environment: Environment | str | object = _UNSET,
    log_level: LogLevel | str | object = _UNSET,
    log_format: LogFormat | str | object = _UNSET,
    log_dir: Path | str | None | object = _UNSET,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from explicit arguments, environment variables, and defaults."""
    source = os.environ if environ is None else environ
    return Settings(
        environment=_parse_required_environment(
            _select_value(environment, source, EnvVarName.ENVIRONMENT)
        ),
        log_level=_parse_log_level(
            _select_value(
                log_level, source, EnvVarName.LOG_LEVEL, default=LogLevel.INFO
            )
        ),
        log_format=_parse_log_format(
            _select_value(
                log_format, source, EnvVarName.LOG_FORMAT, default=LogFormat.JSON
            )
        ),
        log_dir=_parse_log_dir(
            _select_value(log_dir, source, EnvVarName.LOG_DIR, default=None)
        ),
    )


def _select_value(
    explicit: object,
    environ: Mapping[str, str],
    name: EnvVarName,
    *,
    default: object = _UNSET,
) -> object:
    if explicit is not _UNSET:
        return explicit
    if name.value in environ:
        return environ[name.value]
    return default


def _parse_required_environment(value: object) -> Environment:
    if value is _UNSET:
        raise ConfigError(f"{EnvVarName.ENVIRONMENT.value} is required")
    if not isinstance(value, str):
        raise ConfigError(f"{EnvVarName.ENVIRONMENT.value} must be a string")
    if value == "":
        raise ConfigError(f"{EnvVarName.ENVIRONMENT.value} must not be empty")
    try:
        return Environment(value.lower())
    except ValueError as exc:
        expected = ", ".join(item.value for item in Environment)
        raise ConfigError(
            f"{EnvVarName.ENVIRONMENT.value} must be one of: {expected}"
        ) from exc


def _parse_log_level(value: object) -> LogLevel:
    if isinstance(value, LogLevel):
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{EnvVarName.LOG_LEVEL.value} must be a string")
    if value == "":
        raise ConfigError(f"{EnvVarName.LOG_LEVEL.value} must not be empty")
    try:
        return LogLevel(value.upper())
    except ValueError as exc:
        expected = ", ".join(item.value for item in LogLevel)
        raise ConfigError(
            f"{EnvVarName.LOG_LEVEL.value} must be one of: {expected}"
        ) from exc


def _parse_log_format(value: object) -> LogFormat:
    if isinstance(value, LogFormat):
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{EnvVarName.LOG_FORMAT.value} must be a string")
    if value == "":
        raise ConfigError(f"{EnvVarName.LOG_FORMAT.value} must not be empty")
    try:
        return LogFormat(value.lower())
    except ValueError as exc:
        expected = ", ".join(item.value for item in LogFormat)
        raise ConfigError(
            f"{EnvVarName.LOG_FORMAT.value} must be one of: {expected}"
        ) from exc


def _parse_log_dir(value: object) -> Path | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value == "":
            return None
        return Path(value)
    if isinstance(value, Path):
        return value
    raise ConfigError(f"{EnvVarName.LOG_DIR.value} must be a path string")
