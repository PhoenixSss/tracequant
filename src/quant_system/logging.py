"""Structured application logging with secret-safe JSON output."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, TextIO, cast

from quant_system.config import LogFormat, SecretValue, Settings
from quant_system.core.time import UTC, format_utc

__all__ = [
    "FIELD_LEVEL",
    "FIELD_LOGGER",
    "FIELD_MESSAGE",
    "FIELD_TIMESTAMP",
    "LOG_FILE_NAME",
    "LoggingConfigError",
    "REDACTED_VALUE",
    "configure_logging",
    "redact_sensitive",
]

FIELD_TIMESTAMP: Final = "timestamp"
FIELD_LEVEL: Final = "level"
FIELD_LOGGER: Final = "logger"
FIELD_MESSAGE: Final = "message"
FIELD_EXCEPTION: Final = "exception"
FIELD_EXTRA: Final = "extra"
LOG_FILE_NAME: Final = "quant-system.jsonl"
REDACTED_VALUE: Final = "<redacted>"

_HANDLER_MARKER: Final = "_quant_system_logging_handler"
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
)
_STANDARD_LOG_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {*logging.makeLogRecord({}).__dict__, "message"}
)


class LoggingConfigError(ValueError):
    """Raised when logging cannot be configured safely."""


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            FIELD_TIMESTAMP: format_utc(datetime.fromtimestamp(record.created, UTC)),
            FIELD_LEVEL: record.levelname,
            FIELD_LOGGER: record.name,
            FIELD_MESSAGE: _safe_message(record),
        }
        extra = _safe_extra(record)
        if extra:
            payload[FIELD_EXTRA] = extra
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload[FIELD_EXCEPTION] = _safe_exception(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(
    settings: Settings,
    *,
    stream: TextIO | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Configure project-owned JSON handlers from explicit settings."""
    if settings.log_format is not LogFormat.JSON:
        raise LoggingConfigError("only json log format is supported")

    target_logger = logging.getLogger() if logger is None else logger
    target_logger.setLevel(settings.log_level.value)
    _remove_project_handlers(target_logger)

    formatter = _JsonFormatter()
    console_handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    _mark_project_handler(console_handler)
    console_handler.setFormatter(formatter)
    target_logger.addHandler(console_handler)

    if settings.log_dir is not None:
        log_dir = _prepare_log_dir(settings.log_dir)
        file_handler = logging.FileHandler(
            log_dir / LOG_FILE_NAME, mode="a", encoding="utf-8"
        )
        _mark_project_handler(file_handler)
        file_handler.setFormatter(formatter)
        target_logger.addHandler(file_handler)


def redact_sensitive(value: object) -> object:
    """Return a JSON-safe value with known sensitive keys recursively redacted."""
    return _redact(value, sensitive_context=False)


def _remove_project_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def _mark_project_handler(handler: logging.Handler) -> None:
    setattr(handler, _HANDLER_MARKER, True)


def _prepare_log_dir(log_dir: Path) -> Path:
    if log_dir.exists() and not log_dir.is_dir():
        raise LoggingConfigError(f"log path is not a directory: {log_dir}")
    log_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(log_dir, os.W_OK):
        raise LoggingConfigError(f"log directory is not writable: {log_dir}")
    return log_dir


def _safe_message(record: logging.LogRecord) -> str:
    original_args = record.args
    try:
        if isinstance(original_args, Mapping):
            record.args = cast(Mapping[str, object], redact_sensitive(original_args))
        elif isinstance(original_args, tuple):
            record.args = tuple(
                _redact(item, sensitive_context=False) for item in original_args
            )
        return record.getMessage()
    finally:
        record.args = original_args


def _safe_extra(record: logging.LogRecord) -> dict[str, object]:
    extra: dict[str, object] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOG_RECORD_KEYS or key.startswith("_"):
            continue
        redacted = cast(Mapping[str, object], redact_sensitive({key: value}))
        extra[key] = redacted[key]
    return extra


def _safe_exception(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None],
) -> dict[str, object]:
    exc_type, exc, _exc_traceback = exc_info
    redacted_args = redact_sensitive(exc.args)
    return {
        "type": exc_type.__name__,
        "message": str(redacted_args[0] if len(exc.args) == 1 else redacted_args),  # type: ignore[index]
    }


def _redact(value: object, *, sensitive_context: bool) -> object:
    if sensitive_context:
        return REDACTED_VALUE
    if isinstance(value, SecretValue):
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return {
            str(key): _redact(
                item,
                sensitive_context=str(key).lower() in _SENSITIVE_KEYS,
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_redact(item, sensitive_context=False) for item in value]
    if isinstance(value, list):
        return [_redact(item, sensitive_context=False) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redact(item, sensitive_context=False) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _redact(asdict(value), sensitive_context=False)
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value
