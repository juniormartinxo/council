import json
from pathlib import Path

from council.config import FlowStep
import council.flow_tui as flow_tui_module
from council.flow_tui import DEFAULT_INPUT_TEMPLATE, FlowConfigApp


def _build_step(input_template: str) -> FlowStep:
    return FlowStep(
        key="plan",
        agent_name="Agent",
        role_desc="Role",
        command="codex exec --skip-git-repo-check",
        instruction="instruction",
        input_template=input_template,
    )


def _save_and_load_payload(tmp_path: Path, step: FlowStep) -> dict[str, object]:
    output_path = tmp_path / "flow.json"
    app = FlowConfigApp(config_path=output_path)
    app.notify = lambda *args, **kwargs: None
    app.steps = [step]
    app._execute_save()
    return json.loads(output_path.read_text(encoding="utf-8"))


def test_execute_save_omits_default_input_template(tmp_path: Path) -> None:
    payload = _save_and_load_payload(tmp_path, _build_step(DEFAULT_INPUT_TEMPLATE))
    step = payload["steps"][0]
    assert "input_template" not in step


def test_execute_save_omits_empty_input_template(tmp_path: Path) -> None:
    payload = _save_and_load_payload(tmp_path, _build_step(""))
    step = payload["steps"][0]
    assert "input_template" not in step


class _StubInput:
    def __init__(self, value: str) -> None:
        self.value = value


class _StubSelect:
    def __init__(self, value: str) -> None:
        self.value = value


class _StubTextArea:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubCheckbox:
    def __init__(self, value: bool) -> None:
        self.value = value


class _StubListView:
    def __init__(self) -> None:
        self.children: list[object] = []


class _StubStepListItem:
    def __init__(self, step: FlowStep, step_index: int) -> None:
        self.step = step
        self.step_index = step_index

    def update_label(self) -> None:  # pragma: no cover - sem efeito no teste
        pass


class _StubReorderListView:
    def __init__(self, children: list[_StubStepListItem]) -> None:
        self.children = children
        self.index: int | None = None

    def move_child(self, child: int, *, before=None, after=None) -> None:
        moving_item = self.children[child]
        if before is not None:
            target_item = self.children[before]
        elif after is not None:
            target_item = self.children[after]
        else:  # pragma: no cover - não usado
            raise ValueError("before/after obrigatório")

        self.children.remove(moving_item)
        target_index = self.children.index(target_item)
        insert_index = target_index if before is not None else target_index + 1
        self.children.insert(insert_index, moving_item)


def test_save_form_to_step_replaces_frozen_flow_step() -> None:
    app = FlowConfigApp(config_path=None)
    original_step = _build_step("template-original")
    app.steps = [original_step]

    widgets = {
        "#sel-step-profile": _StubSelect("new-key||new-role"),
        "#sel-agent-name": _StubSelect("new-agent"),
        "#sel-style": _StubSelect("new-style"),
        "#in-command": _StubInput("codex exec --skip-git-repo-check"),
        "#ta-instruction": _StubTextArea("new instruction"),
        "#ta-input-template": _StubTextArea(""),
        "#cb-is-code": _StubCheckbox(True),
        "#in-timeout": _StubInput("90"),
        "#in-max-input": _StubInput("1000"),
        "#in-max-output": _StubInput("2000"),
        "#in-max-context": _StubInput("3000"),
        "#step-list": _StubListView(),
    }

    app.query_one = lambda selector, _widget_type=None: widgets[selector]  # type: ignore[assignment]
    app._save_form_to_step(0)

    updated_step = app.steps[0]
    assert updated_step is not original_step
    assert updated_step.key == "new-key"
    assert updated_step.agent_name == "new-agent"
    assert updated_step.role_desc == "new-role"
    assert updated_step.style == "new-style"
    assert updated_step.command == "codex exec --skip-git-repo-check"
    assert updated_step.instruction == "new instruction"
    assert updated_step.input_template == DEFAULT_INPUT_TEMPLATE
    assert updated_step.is_code is True
    assert updated_step.timeout == 90
    assert updated_step.max_input_chars == 1000
    assert updated_step.max_output_chars == 2000
    assert updated_step.max_context_chars == 3000


def test_resolve_default_command_for_agent() -> None:
    assert FlowConfigApp._resolve_default_command_for_agent("Gemini") == "gemini -p {input}"
    assert FlowConfigApp._resolve_default_command_for_agent("Codex") == "codex exec --skip-git-repo-check"
    assert FlowConfigApp._resolve_default_command_for_agent("Desconhecido") is None


def test_load_initial_data_populates_first_step(monkeypatch) -> None:
    sample_step = _build_step("template")
    app = FlowConfigApp(config_path=None)

    class _StubListView:
        def __init__(self) -> None:
            self.children = [object()]
            self.index = None

    list_view = _StubListView()
    captured: dict[str, object] = {}

    monkeypatch.setattr(flow_tui_module, "get_default_flow_steps", lambda: [sample_step])
    app._refresh_list = lambda: None
    app.query_one = lambda selector, _widget_type=None: list_view if selector == "#step-list" else None  # type: ignore[assignment]
    app._populate_form = lambda step: captured.update({"step": step})

    app._load_initial_data()

    assert app.current_step_index == 0
    assert captured["step"] is sample_step
    assert list_view.index == 0


def test_move_up_keeps_order_and_selection_consistent() -> None:
    step_a = _build_step("template-a")
    step_b = FlowStep(
        key="critique",
        agent_name="Gemini",
        role_desc="Crítica",
        command="gemini -p {input}",
        instruction="instruction-b",
        input_template="template-b",
    )
    step_c = FlowStep(
        key="code",
        agent_name="Codex",
        role_desc="Implementação",
        command="codex exec --skip-git-repo-check",
        instruction="instruction-c",
        input_template="template-c",
    )

    app = FlowConfigApp(config_path=None)
    app.steps = [step_a, step_b, step_c]
    app.current_step_index = 1
    app._save_form_to_step = lambda _idx: None  # type: ignore[assignment]
    app._populate_form = lambda _step: None  # type: ignore[assignment]

    list_view = _StubReorderListView(
        [
            _StubStepListItem(step_a, 0),
            _StubStepListItem(step_b, 1),
            _StubStepListItem(step_c, 2),
        ]
    )
    app.query_one = lambda selector, _widget_type=None: list_view if selector == "#step-list" else None  # type: ignore[assignment]

    app._move_up()

    assert [step.key for step in app.steps] == ["critique", "plan", "code"]
    assert app.current_step_index == 0
    assert list_view.index == 0
    assert [item.step.key for item in list_view.children] == ["critique", "plan", "code"]
    assert [item.step_index for item in list_view.children] == [0, 1, 2]


def test_move_down_keeps_order_and_selection_consistent() -> None:
    step_a = _build_step("template-a")
    step_b = FlowStep(
        key="critique",
        agent_name="Gemini",
        role_desc="Crítica",
        command="gemini -p {input}",
        instruction="instruction-b",
        input_template="template-b",
    )
    step_c = FlowStep(
        key="code",
        agent_name="Codex",
        role_desc="Implementação",
        command="codex exec --skip-git-repo-check",
        instruction="instruction-c",
        input_template="template-c",
    )

    app = FlowConfigApp(config_path=None)
    app.steps = [step_a, step_b, step_c]
    app.current_step_index = 1
    app._save_form_to_step = lambda _idx: None  # type: ignore[assignment]
    app._populate_form = lambda _step: None  # type: ignore[assignment]

    list_view = _StubReorderListView(
        [
            _StubStepListItem(step_a, 0),
            _StubStepListItem(step_b, 1),
            _StubStepListItem(step_c, 2),
        ]
    )
    app.query_one = lambda selector, _widget_type=None: list_view if selector == "#step-list" else None  # type: ignore[assignment]

    app._move_down()

    assert [step.key for step in app.steps] == ["plan", "code", "critique"]
    assert app.current_step_index == 2
    assert list_view.index == 2
    assert [item.step.key for item in list_view.children] == ["plan", "code", "critique"]
    assert [item.step_index for item in list_view.children] == [0, 1, 2]
