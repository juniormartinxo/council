import json
from pathlib import Path

import pytest

import council.config as config_module
from council.config import ConfigError, FlowStep, load_flow_steps, render_step_input
from council.config import FLOW_CONFIG_ENV_VAR, resolve_flow_config
from council.flow_signature import FLOW_SIGNATURE_REQUIRED_ENV_VAR
from council.paths import COUNCIL_HOME_ENV_VAR


@pytest.fixture(autouse=True)
def mock_command_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    known_bins = {
        "cat": "/usr/bin/cat",
        "claude": "/usr/local/bin/claude",
        "codex": "/usr/local/bin/codex",
        "echo": "/usr/bin/echo",
        "gemini": "/usr/local/bin/gemini",
        "ollama": "/usr/local/bin/ollama",
    }

    monkeypatch.setattr("council.config.shutil.which", lambda binary: known_bins.get(binary))


def _step_payload(key: str = "step_1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "key": key,
        "agent_name": "Agent",
        "role_desc": "Role",
        "command": "codex exec",
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
    monkeypatch.delenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, raising=False)
    return tmp_path


def test_load_flow_steps_returns_defaults_when_config_is_missing(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(isolated_config_env)

    steps = load_flow_steps(None)

    assert [step.key for step in steps] == ["plan", "critique", "final_plan", "code", "review"]


def test_resolve_flow_config_marks_default_source_when_no_file_found(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(isolated_config_env)

    resolved = resolve_flow_config(None)

    assert resolved.path is None
    assert resolved.source == config_module.FLOW_CONFIG_SOURCE_DEFAULT


def test_resolve_flow_config_marks_cwd_source(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd_flow = isolated_config_env / "flow.json"
    _write_json(cwd_flow, [_step_payload(key="from_cwd")])
    monkeypatch.chdir(isolated_config_env)

    resolved = resolve_flow_config(None)

    assert resolved.path == cwd_flow
    assert resolved.source == config_module.FLOW_CONFIG_SOURCE_CWD


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


def test_load_flow_steps_uses_pre_resolved_config_without_re_resolving(
    isolated_config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_path = isolated_config_env / "cli.json"
    cwd_path = isolated_config_env / "flow.json"
    _write_json(cli_path, [_step_payload(key="from_cli")])
    _write_json(cwd_path, [_step_payload(key="from_cwd")])
    monkeypatch.chdir(isolated_config_env)

    resolved_cli = resolve_flow_config(str(cli_path))
    steps = load_flow_steps(None, resolved_config=resolved_cli)

    assert steps[0].key == "from_cli"


def test_load_flow_steps_raises_on_invalid_json(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.json"
    bad_file.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON inválido"):
        load_flow_steps(str(bad_file))


def test_load_flow_steps_rejects_unsigned_flow_when_signature_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload()])
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "1")

    with pytest.raises(ConfigError, match="Assinatura ausente"):
        load_flow_steps(str(path))


def test_load_flow_steps_fails_on_invalid_signature_requirement_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload()])
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "talvez")

    with pytest.raises(ConfigError, match=FLOW_SIGNATURE_REQUIRED_ENV_VAR):
        load_flow_steps(str(path))


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


def test_load_flow_steps_parses_optional_runtime_limits(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(
        path,
        [
            _step_payload(
                timeout=300,
                max_input_chars=1200,
                max_output_chars=2500,
                max_context_chars=5000,
            )
        ],
    )

    steps = load_flow_steps(str(path))

    assert steps[0].timeout == 300
    assert steps[0].max_input_chars == 1200
    assert steps[0].max_output_chars == 2500
    assert steps[0].max_context_chars == 5000


@pytest.mark.parametrize("field_name", ["timeout", "max_input_chars", "max_output_chars", "max_context_chars"])
@pytest.mark.parametrize("invalid_value", [0, -1, "x", True])
def test_load_flow_steps_rejects_invalid_optional_runtime_limits(
    tmp_path: Path,
    field_name: str,
    invalid_value: object,
) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(**{field_name: invalid_value})])

    with pytest.raises(ConfigError, match=field_name):
        load_flow_steps(str(path))


@pytest.mark.parametrize(
    ("command", "operator"),
    [
        ("codex exec | cat", "|"),
        ("codex exec && cat", "&&"),
        ("codex exec;cat", ";"),
        ("codex exec `whoami`", "`"),
        ("codex exec $(whoami)", "$("),
        ("codex exec > /tmp/x", ">"),
        ("codex exec >> /tmp/x", ">>"),
        ("codex exec\nwhoami", "\\n"),
        ("codex exec\rwhoami", "\\r"),
        ("codex exec $HOME", "$VAR"),
        ("codex exec ${HOME}", "${"),
        ("codex exec ~/tmp", "~"),
    ],
)
def test_load_flow_steps_raises_on_disallowed_shell_operators(
    tmp_path: Path, command: str, operator: str
) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(command=command)])

    with pytest.raises(ConfigError, match="operadores de shell não permitidos") as exc_info:
        load_flow_steps(str(path))
    assert operator in str(exc_info.value)


def test_load_flow_steps_raises_on_unknown_command_binary(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(command="missing_binary --flag")])

    with pytest.raises(ConfigError, match="binário inexistente no PATH"):
        load_flow_steps(str(path))


def test_load_flow_steps_raises_on_non_allowlisted_binary(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(command="echo test")])

    with pytest.raises(ConfigError, match="binário não permitido"):
        load_flow_steps(str(path))


def test_load_flow_steps_raises_on_binary_path_instead_of_name(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(command="/usr/local/bin/codex exec")])

    with pytest.raises(ConfigError, match="sem caminho explícito"):
        load_flow_steps(str(path))


def test_load_flow_steps_raises_on_invalid_command_syntax(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    _write_json(path, [_step_payload(command="codex 'unterminated")])

    with pytest.raises(ConfigError, match="sintaxe inválida"):
        load_flow_steps(str(path))


def test_default_flow_commands_follow_validation_rules() -> None:
    for index, step in enumerate(config_module.get_default_flow_steps(), start=1):
        config_module._validate_command(step.command, step=index)


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
