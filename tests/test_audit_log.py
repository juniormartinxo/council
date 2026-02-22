import json
import logging
import stat
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

