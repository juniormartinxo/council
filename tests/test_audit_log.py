import json
import logging
import os
import stat
import threading
from pathlib import Path

import pytest

import council.audit_log as audit_log_module
from council.paths import COUNCIL_HOME_ENV_VAR


def _flush_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()


def _read_log_entries(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _reset_audit_logger() -> None:
    audit_log_module._reset_audit_logger_for_tests()
    yield
    audit_log_module._reset_audit_logger_for_tests()


def test_audit_log_writes_json_entries_with_secured_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.delenv(audit_log_module.COUNCIL_LOG_LEVEL_ENV_VAR, raising=False)

    logger = audit_log_module.get_audit_logger()
    audit_log_module.log_event(
        logger,
        "audit.test.entry",
        level=logging.INFO,
        command="codex exec --skip-git-repo-check",
        return_code=0,
    )
    _flush_logger(logger)

    log_path = council_home / audit_log_module.COUNCIL_LOG_FILE_NAME
    assert log_path.exists()
    assert stat.S_IMODE(council_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600

    entries = _read_log_entries(log_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "audit.test.entry"
    assert entry["level"] == "INFO"
    assert entry["data"]["command"] == "codex exec --skip-git-repo-check"
    assert entry["data"]["return_code"] == 0


def test_audit_log_respects_minimum_level_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.setenv(audit_log_module.COUNCIL_LOG_LEVEL_ENV_VAR, "ERROR")

    logger = audit_log_module.get_audit_logger()
    audit_log_module.log_event(logger, "audit.test.info", level=logging.INFO)
    audit_log_module.log_event(logger, "audit.test.error", level=logging.ERROR, detail="failure")
    _flush_logger(logger)

    log_path = council_home / audit_log_module.COUNCIL_LOG_FILE_NAME
    entries = _read_log_entries(log_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "audit.test.error"
    assert entries[0]["level"] == "ERROR"
    assert entries[0]["data"]["detail"] == "failure"


def test_audit_log_rejects_invalid_log_level_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(audit_log_module.COUNCIL_LOG_LEVEL_ENV_VAR, "invalid-level")

    with pytest.raises(ValueError, match=audit_log_module.COUNCIL_LOG_LEVEL_ENV_VAR):
        audit_log_module.get_audit_logger()


def test_audit_log_sets_propagate_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))

    logger = audit_log_module.get_audit_logger()

    assert logger.propagate is False


def test_audit_log_falls_back_to_null_handler_when_council_home_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_get_home(*, create: bool = False) -> Path:
        del create
        raise OSError("sem permissão para criar COUNCIL_HOME")

    monkeypatch.setattr(audit_log_module, "get_council_home", failing_get_home)

    logger = audit_log_module.get_audit_logger()
    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)

    # Não deve levantar exceção mesmo sem destino de arquivo.
    audit_log_module.log_event(logger, "audit.test.nullhandler", level=logging.INFO)


def test_audit_log_reapplies_file_permissions_on_each_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))

    logger = audit_log_module.get_audit_logger()
    audit_log_module.log_event(logger, "audit.test.first", level=logging.INFO)
    _flush_logger(logger)

    log_path = council_home / audit_log_module.COUNCIL_LOG_FILE_NAME
    os.chmod(log_path, 0o666)
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o666

    audit_log_module.log_event(logger, "audit.test.second", level=logging.INFO)
    _flush_logger(logger)

    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_audit_log_rotates_when_file_exceeds_max_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.setenv(audit_log_module.COUNCIL_LOG_MAX_BYTES_ENV_VAR, "400")
    monkeypatch.setenv(audit_log_module.COUNCIL_LOG_BACKUP_COUNT_ENV_VAR, "2")

    logger = audit_log_module.get_audit_logger()
    for index in range(20):
        audit_log_module.log_event(
            logger,
            "audit.test.rotation",
            level=logging.INFO,
            index=index,
            payload="x" * 120,
        )
    _flush_logger(logger)

    rotated_file = council_home / f"{audit_log_module.COUNCIL_LOG_FILE_NAME}.1"
    assert rotated_file.exists()


def test_reset_audit_logger_for_tests_is_thread_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    errors: list[Exception] = []

    def configure_and_log() -> None:
        try:
            for _ in range(120):
                logger = audit_log_module.get_audit_logger()
                audit_log_module.log_event(logger, "audit.test.concurrent", level=logging.INFO)
        except Exception as exc:  # pragma: no cover - defensivo
            errors.append(exc)

    def reset_loop() -> None:
        try:
            for _ in range(120):
                audit_log_module._reset_audit_logger_for_tests()
        except Exception as exc:  # pragma: no cover - defensivo
            errors.append(exc)

    workers = [
        threading.Thread(target=configure_and_log),
        threading.Thread(target=configure_and_log),
        threading.Thread(target=reset_loop),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []

    logger = audit_log_module.get_audit_logger()
    audit_log_module.log_event(logger, "audit.test.after_concurrency", level=logging.INFO)
    _flush_logger(logger)

    log_path = council_home / audit_log_module.COUNCIL_LOG_FILE_NAME
    assert log_path.exists()
