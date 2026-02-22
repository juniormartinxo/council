from contextlib import contextmanager

from council.config import FlowStep
from council.orchestrator import Orchestrator
from council.state import CouncilState


class DummyExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_cli(
        self,
        command: str,
        input_data: str,
        timeout: int = 120,
        on_output=None,
        max_input_chars: int | None = None,
        max_output_chars: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "command": command,
                "input_data": input_data,
                "timeout": timeout,
                "max_input_chars": max_input_chars,
                "max_output_chars": max_output_chars,
            }
        )
        if on_output:
            on_output("chunk")
        return "resultado"


class DummyUI:
    def __init__(self) -> None:
        self.console = self
        self.errors: list[str] = []

    def print(self, *args, **kwargs) -> None:
        del args, kwargs

    @contextmanager
    def live_stream(self, title: str, style: str = "blue", max_height: int = 10):
        del title, style, max_height

        def update(_: str) -> None:
            return None

        yield update

    def show_panel(self, title: str, content: str, style: str = "blue", is_code: bool = False) -> None:
        del title, content, style, is_code

    def show_success(self, message: str) -> None:
        del message

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class DummyHistoryStore:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.finish_calls: list[dict[str, object]] = []
        self.step_calls: list[dict[str, object]] = []

    def start_run(
        self,
        *,
        prompt: str,
        flow_config_path: str | None,
        flow_config_source: str | None,
        planned_steps: int,
        started_at_utc: str | None = None,
    ) -> int:
        self.start_calls.append(
            {
                "prompt": prompt,
                "flow_config_path": flow_config_path,
                "flow_config_source": flow_config_source,
                "planned_steps": planned_steps,
                "started_at_utc": started_at_utc,
            }
        )
        return 42

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
        self.finish_calls.append(
            {
                "run_id": run_id,
                "status": status,
                "error_message": error_message,
                "executed_steps": executed_steps,
                "successful_steps": successful_steps,
                "duration_ms": duration_ms,
                "finished_at_utc": finished_at_utc,
            }
        )

    def record_step(self, **payload: object) -> None:
        self.step_calls.append(payload)


def test_orchestrator_forwards_runtime_limits_to_executor() -> None:
    state = CouncilState(max_context_chars=500)
    ui = DummyUI()
    executor = DummyExecutor()
    step = FlowStep(
        key="only",
        agent_name="Agent",
        role_desc="Role",
        command="claude -p",
        instruction="Instrucao",
        input_template="{full_context}",
        timeout=77,
        max_input_chars=123,
        max_output_chars=456,
        max_context_chars=60,
    )
    orchestrator = Orchestrator(state, executor, ui, flow_steps=[step])

    orchestrator.run_flow("X" * 300)

    assert ui.errors == []
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["timeout"] == 77
    assert call["max_input_chars"] == 123
    assert call["max_output_chars"] == 456
    assert len(str(call["input_data"])) <= 60


def test_orchestrator_persists_run_and_steps_in_history_store() -> None:
    state = CouncilState(max_context_chars=500)
    ui = DummyUI()
    executor = DummyExecutor()
    history_store = DummyHistoryStore()
    step = FlowStep(
        key="only",
        agent_name="Agent",
        role_desc="Role",
        command="claude -p",
        instruction="Instrucao",
        input_template="{full_context}",
        timeout=45,
        max_input_chars=321,
        max_output_chars=654,
        max_context_chars=80,
    )
    orchestrator = Orchestrator(
        state,
        executor,
        ui,
        flow_steps=[step],
        history_store=history_store,
        flow_config_path="flow.example.json",
        flow_config_source="cli",
    )

    orchestrator.run_flow("Prompt inicial")

    assert len(history_store.start_calls) == 1
    assert history_store.start_calls[0]["prompt"] == "Prompt inicial"
    assert history_store.start_calls[0]["planned_steps"] == 1
    assert history_store.start_calls[0]["flow_config_path"] == "flow.example.json"
    assert history_store.start_calls[0]["flow_config_source"] == "cli"

    assert len(history_store.step_calls) == 1
    step_call = history_store.step_calls[0]
    assert step_call["run_id"] == 42
    assert step_call["step_key"] == "only"
    assert step_call["status"] == "success"
    assert step_call["output_data"] == "resultado"
    assert step_call["timeout_seconds"] == 45
    assert step_call["max_context_chars"] == 80

    assert len(history_store.finish_calls) == 1
    finish_call = history_store.finish_calls[0]
    assert finish_call["run_id"] == 42
    assert finish_call["status"] == "success"
    assert finish_call["executed_steps"] == 1
    assert finish_call["successful_steps"] == 1
