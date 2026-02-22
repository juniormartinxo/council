from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from council.paths import get_run_history_db_path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    """Persistência estruturada de runs e steps em SQLite local."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or get_run_history_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._secure_directory_permissions(self.db_path.parent)
        self._initialize_schema()
        self._secure_file_permissions(self.db_path)

    def start_run(
        self,
        *,
        prompt: str,
        flow_config_path: str | None,
        flow_config_source: str | None,
        planned_steps: int,
        started_at_utc: str | None = None,
    ) -> int:
        started = started_at_utc or utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    prompt,
                    flow_config_path,
                    flow_config_source,
                    status,
                    error_message,
                    started_at_utc,
                    finished_at_utc,
                    duration_ms,
                    planned_steps,
                    executed_steps,
                    successful_steps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt,
                    flow_config_path,
                    flow_config_source,
                    "running",
                    None,
                    started,
                    None,
                    None,
                    max(0, planned_steps),
                    0,
                    0,
                ),
            )
            run_id = int(cursor.lastrowid)
        self._secure_file_permissions(self.db_path)
        return run_id

    def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        error_message: str | None,
        executed_steps: int,
        successful_steps: int,
        duration_ms: int,
        finished_at_utc: str | None = None,
    ) -> None:
        finished = finished_at_utc or utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET
                    status = ?,
                    error_message = ?,
                    finished_at_utc = ?,
                    duration_ms = ?,
                    executed_steps = ?,
                    successful_steps = ?
                WHERE id = ?
                """,
                (
                    status,
                    error_message,
                    finished,
                    max(0, duration_ms),
                    max(0, executed_steps),
                    max(0, successful_steps),
                    run_id,
                ),
            )
        self._secure_file_permissions(self.db_path)

    def record_step(
        self,
        *,
        run_id: int,
        sequence: int,
        step_key: str,
        agent_name: str,
        role_desc: str,
        command: str,
        input_data: str,
        output_data: str,
        status: str,
        error_message: str | None,
        timeout_seconds: int,
        max_input_chars: int | None,
        max_output_chars: int | None,
        max_context_chars: int | None,
        is_feedback: bool,
        started_at_utc: str,
        finished_at_utc: str,
        duration_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO run_steps (
                    run_id,
                    sequence,
                    step_key,
                    agent_name,
                    role_desc,
                    command,
                    input_data,
                    output_data,
                    status,
                    error_message,
                    timeout_seconds,
                    max_input_chars,
                    max_output_chars,
                    max_context_chars,
                    is_feedback,
                    started_at_utc,
                    finished_at_utc,
                    duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    max(1, sequence),
                    step_key,
                    agent_name,
                    role_desc,
                    command,
                    input_data,
                    output_data,
                    status,
                    error_message,
                    timeout_seconds,
                    max_input_chars,
                    max_output_chars,
                    max_context_chars,
                    1 if is_feedback else 0,
                    started_at_utc,
                    finished_at_utc,
                    max(0, duration_ms),
                ),
            )
        self._secure_file_permissions(self.db_path)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        effective_limit = max(1, limit)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT
                    id,
                    status,
                    started_at_utc,
                    finished_at_utc,
                    duration_ms,
                    planned_steps,
                    executed_steps,
                    successful_steps,
                    flow_config_path,
                    flow_config_source
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (effective_limit,),
            )
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    flow_config_path TEXT,
                    flow_config_source TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT,
                    duration_ms INTEGER,
                    planned_steps INTEGER NOT NULL DEFAULT 0,
                    executed_steps INTEGER NOT NULL DEFAULT 0,
                    successful_steps INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS run_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    step_key TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    role_desc TEXT NOT NULL,
                    command TEXT NOT NULL,
                    input_data TEXT NOT NULL,
                    output_data TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    timeout_seconds INTEGER NOT NULL,
                    max_input_chars INTEGER,
                    max_output_chars INTEGER,
                    max_context_chars INTEGER,
                    is_feedback INTEGER NOT NULL DEFAULT 0,
                    started_at_utc TEXT NOT NULL,
                    finished_at_utc TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_run_steps_run_id_sequence
                    ON run_steps (run_id, sequence);
                """
            )

    def _secure_file_permissions(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _secure_directory_permissions(self, directory: Path) -> None:
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
