import json
from pathlib import Path

import pytest

from council.config import ConfigError, FlowStep, load_flow_steps, render_step_input
from council.config import FLOW_CONFIG_ENV_VAR
from council.paths import COUNCIL_HOME_ENV_VAR


def _step_payload(key: str = "step_1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "key": key,
        "agent_name": "Agent",
        "role_desc": "Role",
        "command": "echo test",
        "instruction": "Run",
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def isolated_config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    monkeypatch.delenv(FLOW_CONFIG_ENV_VAR, raising=False)
    return tmp_path


def test_load_flow_steps_returns_defaults_when_config_is_missing(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(isolated_config_env)

    steps = load_flow_steps(None)

    assert [step.key for step in steps] == ["plan", "critique", "final_plan", "code", "review"]


def test_load_flow_steps_reads_flow_json_from_cwd(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(isolated_config_env / "flow.json", [_step_payload(key="from_cwd")])
    monkeypatch.chdir(isolated_config_env)

    steps = load_flow_steps(None)

    assert len(steps) == 1
    assert steps[0].key == "from_cwd"
    assert steps[0].input_template == "{instruction}\n\n{full_context}"


def test_load_flow_steps_prefers_cli_path_over_env_and_cwd(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_path = isolated_config_env / "cli.json"
    env_path = isolated_config_env / "env.json"
    cwd_path = isolated_config_env / "flow.json"

    _write_json(cli_path, [_step_payload(key="from_cli")])
    _write_json(env_path, [_step_payload(key="from_env")])
    _write_json(cwd_path, [_step_payload(key="from_cwd")])

    monkeypatch.setenv(FLOW_CONFIG_ENV_VAR, str(env_path))
    monkeypatch.chdir(isolated_config_env)

    steps = load_flow_steps(str(cli_path))

    assert steps[0].key == "from_cli"


def test_load_flow_steps_raises_on_invalid_json(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.json"
    bad_file.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON inválido"):
        load_flow_steps(str(bad_file))


def test_load_flow_steps_raises_on_duplicate_step_keys(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(key="dup"), _step_payload(key="dup")])

    with pytest.raises(ConfigError, match="duplicadas"):
        load_flow_steps(str(path))


def test_load_flow_steps_raises_on_reserved_step_key(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(key="user_prompt")])

    with pytest.raises(ConfigError, match="nomes reservados"):
        load_flow_steps(str(path))


def test_load_flow_steps_parses_alias_fields_and_is_code(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    payload = {
        "steps": [
            {
                "id": "build",
                "agent": "Codex",
                "role": "Implementacao",
                "command": "codex exec",
                "instruction": "Implemente",
                "is_code": True,
            }
        ]
    }
    _write_json(path, payload)

    steps = load_flow_steps(str(path))

    assert len(steps) == 1
    assert steps[0].key == "build"
    assert steps[0].agent_name == "Codex"
    assert steps[0].role_desc == "Implementacao"
    assert steps[0].is_code is True


def test_render_step_input_raises_for_missing_template_variable() -> None:
    step = FlowStep(
        key="review",
        agent_name="Gemini",
        role_desc="Revisao",
        command="gemini -p {input}",
        instruction="Revise",
        input_template="{instruction}\n\n{missing_field}",
    )

    with pytest.raises(ConfigError, match="missing_field"):
        render_step_input(step, {"instruction": "Revise", "full_context": "ctx"})
