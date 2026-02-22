import sqlite3
from pathlib import Path

from council.history_store import HistoryStore


def test_history_store_persists_runs_and_steps(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "history.sqlite3"
    store = HistoryStore(db_path=db_path)

    run_id = store.start_run(
        prompt="implemente feature X",
        flow_config_path="flow.example.json",
        flow_config_source="cli",
        planned_steps=2,
    )
    store.record_step(
        run_id=run_id,
        sequence=1,
        step_key="architect",
        agent_name="Claude",
        role_desc="Arquitetura",
        command="claude -p",
        input_data="input A",
        output_data="output A",
        status="success",
        error_message=None,
        timeout_seconds=120,
        max_input_chars=1000,
        max_output_chars=2000,
        max_context_chars=3000,
        is_feedback=False,
        started_at_utc="2026-02-22T10:00:00+00:00",
        finished_at_utc="2026-02-22T10:00:01+00:00",
        duration_ms=1000,
    )
    store.finish_run(
        run_id=run_id,
        status="success",
        error_message=None,
        executed_steps=1,
        successful_steps=1,
        duration_ms=1200,
    )

    with sqlite3.connect(db_path) as connection:
        run_row = connection.execute(
            "SELECT status, planned_steps, executed_steps, successful_steps FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        step_row = connection.execute(
            "SELECT step_key, status, output_data FROM run_steps WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchone()

    assert run_row == ("success", 2, 1, 1)
    assert step_row == ("architect", "success", "output A")


def test_history_store_list_runs_orders_by_latest_first(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "history.sqlite3"
    store = HistoryStore(db_path=db_path)

    first_run = store.start_run(
        prompt="primeiro",
        flow_config_path=None,
        flow_config_source="default",
        planned_steps=1,
    )
    second_run = store.start_run(
        prompt="segundo",
        flow_config_path="flow.json",
        flow_config_source="cwd",
        planned_steps=2,
    )

    runs = store.list_runs(limit=10)

    assert runs[0]["id"] == second_run
    assert runs[1]["id"] == first_run
