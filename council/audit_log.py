from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from council.paths import get_council_home, get_council_log_path


COUNCIL_LOG_LEVEL_ENV_VAR = "COUNCIL_LOG_LEVEL"
COUNCIL_LOG_FILE_NAME = "council.log"
DEFAULT_LOG_LEVEL_NAME = "INFO"
LOGGER_NAME = "council.audit"
MAX_FIELD_LENGTH = 500


_LOGGER_LOCK = threading.Lock()
_LOGGER_CONFIGURED = False


class _AuditJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "audit_event", "") or record.getMessage()
        raw_data = getattr(record, "audit_data", {})
        data = raw_data if isinstance(raw_data, Mapping) else {"value": str(raw_data)}

        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            "data": data,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def get_audit_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    global _LOGGER_CONFIGURED
    if _LOGGER_CONFIGURED:
        return logger

    with _LOGGER_LOCK:
        if _LOGGER_CONFIGURED:
            return logger

        _configure_logger(logger)
        _LOGGER_CONFIGURED = True

    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **data: object,
) -> None:
    sanitized_data = {key: _sanitize_log_value(value) for key, value in data.items() if value is not None}
    logger.log(
        level,
        event,
        extra={
            "audit_event": event,
            "audit_data": sanitized_data,
        },
    )


def _configure_logger(logger: logging.Logger) -> None:
    _clear_handlers(logger)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    try:
        home = get_council_home(create=True)
        _secure_directory_permissions(home)
        log_path = get_council_log_path()
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(_resolve_log_level_from_env())
        file_handler.setFormatter(_AuditJsonFormatter())
        logger.addHandler(file_handler)
        _secure_file_permissions(log_path)
    except OSError:
        logger.addHandler(logging.NullHandler())


def _resolve_log_level_from_env() -> int:
    raw_level = os.getenv(COUNCIL_LOG_LEVEL_ENV_VAR, DEFAULT_LOG_LEVEL_NAME).strip().upper()
    return getattr(logging, raw_level, logging.INFO)


def _sanitize_log_value(value: object) -> object:
    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, Path):
        return _truncate_string(str(value))

    if isinstance(value, str):
        return _truncate_string(value)

    if isinstance(value, Mapping):
        return {
            _truncate_string(str(key)): _sanitize_log_value(item_value)
            for key, item_value in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_sanitize_log_value(item) for item in value]

    return _truncate_string(str(value))


def _truncate_string(value: str) -> str:
    if len(value) <= MAX_FIELD_LENGTH:
        return value
    return f"{value[:MAX_FIELD_LENGTH]}...[truncated]"


def _secure_file_permissions(path: Path) -> None:
    if not path.exists():
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _secure_directory_permissions(directory: Path) -> None:
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


def _clear_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def _reset_audit_logger_for_tests() -> None:
    global _LOGGER_CONFIGURED
    with _LOGGER_LOCK:
        logger = logging.getLogger(LOGGER_NAME)
        _clear_handlers(logger)
        _LOGGER_CONFIGURED = False
