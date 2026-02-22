import json
from pathlib import Path

from council.config import FlowStep
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
