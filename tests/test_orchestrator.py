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
