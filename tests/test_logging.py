from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Generator
from datetime import datetime
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest

from tracequant.config import Environment, LogFormat, LogLevel, SecretValue, Settings
from tracequant.core.time import parse_utc
from tracequant.logging import (
    LOG_FILE_NAME,
    REDACTED_VALUE,
    LoggingConfigError,
    configure_logging,
    redact_sensitive,
)

CANARY_SECRET = "canary-secret-task-64-never-log"


@pytest.fixture
def logger() -> Generator[logging.Logger]:
    test_logger = logging.getLogger(f"tracequant.tests.logging.{uuid4().hex}")
    test_logger.handlers.clear()
    test_logger.propagate = False
    yield test_logger
    for handler in list(test_logger.handlers):
        test_logger.removeHandler(handler)
        handler.close()
    test_logger.setLevel(logging.NOTSET)
    test_logger.propagate = True


def test_import_logging_module_has_no_side_effects(
    tmp_path: Path, logger: logging.Logger
) -> None:
    logger.addHandler(logging.NullHandler())
    before_handlers = list(logger.handlers)

    module = importlib.import_module("tracequant.logging")

    assert module.FIELD_TIMESTAMP == "timestamp"
    assert logger.handlers == before_handlers
    assert list(tmp_path.iterdir()) == []


def test_configure_logging_writes_single_line_json_to_console(
    logger: logging.Logger,
) -> None:
    stream = StringIO()
    settings = Settings(environment=Environment.TEST, log_level=LogLevel.INFO)
    configure_logging(settings, stream=stream, logger=logger)

    logger.info("hello %s", "world", extra={"module_field": "research"})

    payload = json.loads(stream.getvalue())
    assert set(payload) >= {"timestamp", "level", "logger", "message"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == logger.name
    assert payload["message"] == "hello world"
    assert payload["extra"] == {"module_field": "research"}
    assert parse_utc(payload["timestamp"]).tzinfo is not None
    assert "\n" not in stream.getvalue().strip()


def test_log_level_comes_from_settings(logger: logging.Logger) -> None:
    stream = StringIO()
    configure_logging(
        Settings(environment=Environment.TEST, log_level=LogLevel.ERROR),
        stream=stream,
        logger=logger,
    )

    logger.warning("hidden")
    logger.error("shown")

    assert json.loads(stream.getvalue())["message"] == "shown"


def test_empty_log_dir_does_not_create_file_handler(logger: logging.Logger) -> None:
    configure_logging(
        Settings(environment=Environment.TEST), stream=StringIO(), logger=logger
    )

    project_handlers = [
        handler
        for handler in logger.handlers
        if handler.formatter is not None
        and handler.formatter.__class__.__module__ == "tracequant.logging"
    ]
    assert len(project_handlers) == 1
    assert not any(
        isinstance(handler, logging.FileHandler) for handler in project_handlers
    )


def test_file_logging_creates_exact_log_directory_and_file(
    tmp_path: Path, logger: logging.Logger
) -> None:
    stream = StringIO()
    log_dir = tmp_path / "logs" / "app"
    configure_logging(
        Settings(environment=Environment.TEST, log_dir=log_dir),
        stream=stream,
        logger=logger,
    )

    logger.info("file output")
    for handler in logger.handlers:
        handler.flush()

    assert log_dir.is_dir()
    log_files = list(log_dir.iterdir())
    assert log_files == [log_dir / LOG_FILE_NAME]
    assert (
        json.loads((log_dir / LOG_FILE_NAME).read_text(encoding="utf-8"))["message"]
        == "file output"
    )


def test_log_dir_that_is_file_fails(tmp_path: Path, logger: logging.Logger) -> None:
    log_path = tmp_path / "not-a-directory"
    log_path.write_text("", encoding="utf-8")

    with pytest.raises(LoggingConfigError, match="not a directory"):
        configure_logging(
            Settings(environment=Environment.TEST, log_dir=log_path),
            stream=StringIO(),
            logger=logger,
        )


def test_repeated_initialization_replaces_only_project_handlers(
    logger: logging.Logger,
) -> None:
    third_party_handler = logging.NullHandler()
    logger.addHandler(third_party_handler)

    configure_logging(
        Settings(environment=Environment.TEST), stream=StringIO(), logger=logger
    )
    configure_logging(
        Settings(environment=Environment.TEST), stream=StringIO(), logger=logger
    )

    assert third_party_handler in logger.handlers
    project_handlers = [
        handler
        for handler in logger.handlers
        if handler.formatter is not None
        and handler.formatter.__class__.__module__ == "tracequant.logging"
    ]
    assert len(project_handlers) == 1


def test_rejects_unsupported_text_format(logger: logging.Logger) -> None:
    with pytest.raises(LoggingConfigError, match="only json"):
        configure_logging(
            Settings(environment=Environment.TEST, log_format=LogFormat.TEXT),
            stream=StringIO(),
            logger=logger,
        )


def test_redact_sensitive_recurses_case_insensitive_keys() -> None:
    value = {
        "Authorization": "Bearer secret",
        "nested": {"api_key": CANARY_SECRET, "safe": "visible"},
        "items": [{"Cookie": CANARY_SECRET}, ("token", {"password": CANARY_SECRET})],
        "similar_tokenized": "visible",
    }

    redacted = redact_sensitive(value)

    assert CANARY_SECRET not in json.dumps(redacted)
    assert redacted["Authorization"] == REDACTED_VALUE  # type: ignore[index]
    assert redacted["nested"]["safe"] == "visible"  # type: ignore[index]
    assert redacted["similar_tokenized"] == "visible"  # type: ignore[index]


def test_secret_canary_is_absent_from_console_file_and_exception(
    tmp_path: Path, logger: logging.Logger
) -> None:
    stream = StringIO()
    log_dir = tmp_path / "logs"
    configure_logging(
        Settings(environment=Environment.TEST, log_dir=log_dir),
        stream=stream,
        logger=logger,
    )

    try:
        raise ValueError({"token": CANARY_SECRET})
    except ValueError:
        logger.exception(
            "failed for %(token)s",
            {"token": CANARY_SECRET},
            extra={
                "settings": {"secret": SecretValue(CANARY_SECRET)},
                "when": datetime(2026, 7, 26, 9, 0),
                "path": Path("logs/example.jsonl"),
            },
        )
    for handler in logger.handlers:
        handler.flush()

    console_output = stream.getvalue()
    file_output = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert CANARY_SECRET not in console_output
    assert CANARY_SECRET not in file_output
    payload = json.loads(console_output)
    assert payload["message"] == f"failed for {REDACTED_VALUE}"
    assert payload["extra"]["settings"]["secret"] == REDACTED_VALUE
    assert REDACTED_VALUE in payload["exception"]["message"]


def test_unicode_and_non_json_extra_values_are_serialized(
    logger: logging.Logger,
) -> None:
    stream = StringIO()
    configure_logging(
        Settings(environment=Environment.TEST), stream=stream, logger=logger
    )

    logger.info(
        "unicode \u6d88\u606f", extra={"path": Path("logs/app"), "raw": object()}
    )

    payload = json.loads(stream.getvalue())
    assert payload["message"] == "unicode \u6d88\u606f"
    assert payload["extra"]["path"] == "logs/app"
    assert isinstance(payload["extra"]["raw"], str)
