from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from council.limits import read_positive_int_env
from council.paths import get_council_home, get_council_log_path


COUNCIL_LOG_LEVEL_ENV_VAR = "COUNCIL_LOG_LEVEL"
COUNCIL_LOG_MAX_BYTES_ENV_VAR = "COUNCIL_LOG_MAX_BYTES"
COUNCIL_LOG_BACKUP_COUNT_ENV_VAR = "COUNCIL_LOG_BACKUP_COUNT"
COUNCIL_LOG_FILE_NAME = "council.log"
DEFAULT_LOG_LEVEL_NAME = "INFO"
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
LOGGER_NAME = "council.audit"
MAX_FIELD_LENGTH = 500

_VALID_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


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


class _SecureRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler com criação/reestrição de permissão em modo 0o600."""

    def _open(self):
        def secure_opener(path: str, flags: int) -> int:
            return os.open(path, flags, 0o600)

        stream = open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            opener=secure_opener,
        )
        self._secure_stream_permissions(stream)
        return stream

    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is not None:
            self._secure_stream_permissions(self.stream)
        super().emit(record)

    def _secure_stream_permissions(self, stream) -> None:
        fileno = getattr(stream, "fileno", None)
        if not callable(fileno):
            return
        try:
            os.fchmod(fileno(), 0o600)
        except OSError:
            pass


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
    log_level = _resolve_log_level_from_env()
    log_max_bytes, log_backup_count = _resolve_rotation_limits_from_env()

    try:
        home = get_council_home(create=True)
        _secure_directory_permissions(home)
        log_path = get_council_log_path()
        file_handler = _SecureRotatingFileHandler(
            log_path,
            maxBytes=log_max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(_AuditJsonFormatter())
        logger.addHandler(file_handler)
        _secure_file_permissions(log_path)
    except OSError:
        logger.addHandler(logging.NullHandler())


def _resolve_log_level_from_env() -> int:
    raw_level = os.getenv(COUNCIL_LOG_LEVEL_ENV_VAR, "").strip()
    if not raw_level:
        return _VALID_LOG_LEVELS[DEFAULT_LOG_LEVEL_NAME]

    normalized_level = raw_level.upper()
    if normalized_level not in _VALID_LOG_LEVELS:
        valid_levels = ", ".join(sorted(_VALID_LOG_LEVELS))
        raise ValueError(
            f"Variável de ambiente '{COUNCIL_LOG_LEVEL_ENV_VAR}' inválida: "
            f"recebido '{raw_level}'. Valores aceitos: {valid_levels}."
        )
    return _VALID_LOG_LEVELS[normalized_level]


def _resolve_rotation_limits_from_env() -> tuple[int, int]:
    max_bytes = read_positive_int_env(COUNCIL_LOG_MAX_BYTES_ENV_VAR, DEFAULT_LOG_MAX_BYTES)
    backup_count = read_positive_int_env(COUNCIL_LOG_BACKUP_COUNT_ENV_VAR, DEFAULT_LOG_BACKUP_COUNT)
    return max_bytes, backup_count


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
