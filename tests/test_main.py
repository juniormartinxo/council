import json
import logging
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import council.main as main_module
from council.config import (
    FLOW_CONFIG_SOURCE_CLI,
    FLOW_CONFIG_SOURCE_CWD,
    FLOW_CONFIG_SOURCE_DEFAULT,
    FLOW_CONFIG_SOURCE_ENV,
    FLOW_CONFIG_SOURCE_USER,
    FlowStep,
    ResolvedFlowConfig,
)
from council.flow_signature import FlowSignatureError
from council.history_store import HistoryStore
from council.paths import COUNCIL_HOME_ENV_VAR
from council.provider_rate_limits import ProviderRateLimitProbeResult


class _DummyUI:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def show_error(self, message: str) -> None:
        self.errors.append(message)


def _state_file(tmp_path: Path) -> Path:
    return tmp_path / ".council-home" / "tui_state.json"


def _sample_step(command: str = "claude -p") -> FlowStep:
    return FlowStep(
        key="step",
        agent_name="Agent",
        role_desc="Role",
        command=command,
        instruction="instruction",
    )


@pytest.fixture(autouse=True)
def _stub_provider_rate_limits_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_resolve_provider_rate_limits", lambda _steps: {})


def test_requires_implicit_flow_confirmation_for_cwd_and_env() -> None:
    cwd_config = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_CWD)
    env_config = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_ENV)

    assert main_module._requires_implicit_flow_confirmation(cwd_config) is True
    assert main_module._requires_implicit_flow_confirmation(env_config) is True


def test_requires_implicit_flow_confirmation_skips_cli_user_and_default() -> None:
    cli_config = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_CLI)
    user_config = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_USER)
    default_config = ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT)

    assert main_module._requires_implicit_flow_confirmation(cli_config) is False
    assert main_module._requires_implicit_flow_confirmation(user_config) is False
    assert main_module._requires_implicit_flow_confirmation(default_config) is False


def test_confirm_implicit_flow_execution_returns_false_without_tty(monkeypatch) -> None:
    resolved = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_CWD)
    stdin_stub = type("StdInStub", (), {"isatty": lambda self: False})()
    monkeypatch.setattr(main_module.sys, "stdin", stdin_stub)

    assert main_module._confirm_implicit_flow_execution(resolved) is False


def test_confirm_implicit_flow_execution_calls_typer_confirm(monkeypatch) -> None:
    resolved = ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_ENV)
    stdin_stub = type("StdInStub", (), {"isatty": lambda self: True})()
    monkeypatch.setattr(main_module.sys, "stdin", stdin_stub)
    captured: dict[str, object] = {}

    def fake_confirm(message: str, default: bool, show_default: bool) -> bool:
        captured["message"] = message
        captured["default"] = default
        captured["show_default"] = show_default
        return True

    monkeypatch.setattr(main_module.typer, "confirm", fake_confirm)

    assert main_module._confirm_implicit_flow_execution(resolved) is True
    assert captured["default"] is False
    assert captured["show_default"] is True
    assert "COUNCIL_FLOW_CONFIG" in str(captured["message"])


def test_run_exits_when_runtime_limits_config_is_invalid(monkeypatch) -> None:
    ui = _DummyUI()

    monkeypatch.setattr(main_module, "UI", lambda: ui)
    monkeypatch.setattr(
        main_module,
        "CouncilState",
        lambda: (_ for _ in ()).throw(ValueError("bad limit")),
    )

    with pytest.raises(typer.Exit) as exc_info:
        main_module.run(prompt="prompt", flow_config=None)

    assert exc_info.value.exit_code == 1
    assert len(ui.errors) == 1
    assert "Configuração inválida de limites" in ui.errors[0]


def test_run_exits_when_logging_config_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _DummyUI()
    monkeypatch.setattr(main_module, "UI", lambda: ui)
    monkeypatch.setattr(
        main_module,
        "get_audit_logger",
        lambda: (_ for _ in ()).throw(ValueError("COUNCIL_LOG_LEVEL inválida")),
    )

    with pytest.raises(typer.Exit) as exc_info:
        main_module.run(prompt="prompt", flow_config=None)

    assert exc_info.value.exit_code == 1
    assert len(ui.errors) == 1
    assert "Configuração inválida de logging" in ui.errors[0]


def test_history_clear_reports_when_state_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["history", "clear"])

    assert result.exit_code == 0
    assert "Nenhum histórico encontrado" in result.stdout


def test_history_clear_removes_sensitive_prompt_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = _state_file(tmp_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "last_prompt": "segredo",
                "prompt_history": ["segredo", "outro segredo"],
                "last_flow_config": "flow.example.json",
                "encrypted_prompt_state": {"version": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(state_file.parent))
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["history", "clear"])

    assert result.exit_code == 0
    assert "Histórico de prompts removido" in result.stdout

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["last_prompt"] == ""
    assert persisted["prompt_history"] == []
    assert persisted["last_flow_config"] == "flow.example.json"
    assert "encrypted_prompt_state" not in persisted


def test_history_runs_reports_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["history", "runs"])

    assert result.exit_code == 0
    assert "Nenhum run persistido" in result.stdout


def test_history_runs_lists_persisted_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    history_store = HistoryStore()
    run_id = history_store.start_run(
        prompt="prompt",
        flow_config_path="flow.example.json",
        flow_config_source="cli",
        planned_steps=1,
    )
    history_store.finish_run(
        run_id=run_id,
        status="success",
        error_message=None,
        executed_steps=1,
        successful_steps=1,
        duration_ms=123,
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["history", "runs", "--limit", "5"])

    assert result.exit_code == 0
    assert f"run={run_id}" in result.stdout
    assert "status=success" in result.stdout


def test_history_clear_forwards_resolved_passphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    captured: dict[str, object] = {}

    monkeypatch.setattr(main_module, "read_tui_state_passphrase", lambda: "senha-vinda-do-ambiente")

    def fake_clear(path: Path, passphrase: str | None = None) -> bool:
        captured["path"] = path
        captured["passphrase"] = passphrase
        return False

    monkeypatch.setattr(main_module, "clear_tui_prompt_history", fake_clear)
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["history", "clear"])

    assert result.exit_code == 0
    assert captured["passphrase"] == "senha-vinda-do-ambiente"


def test_run_exits_when_binary_prerequisite_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _DummyUI()

    monkeypatch.setattr(main_module, "UI", lambda: ui)
    monkeypatch.setattr(main_module, "CouncilState", lambda: object())
    monkeypatch.setattr(main_module, "Executor", lambda _: object())
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT),
    )
    monkeypatch.setattr(
        main_module,
        "load_flow_steps",
        lambda *_args, **_kwargs: [_sample_step("codex exec --skip-git-repo-check")],
    )
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="codex",
                resolved_path=None,
                is_available=False,
            )
        ],
    )

    with pytest.raises(typer.Exit) as exc_info:
        main_module.run(prompt="prompt", flow_config=None)

    assert exc_info.value.exit_code == 1
    assert len(ui.errors) == 1
    assert "Pré-requisitos ausentes no PATH" in ui.errors[0]


def test_doctor_exits_with_error_when_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=Path("/tmp/flow.json"), source=FLOW_CONFIG_SOURCE_CLI),
    )
    monkeypatch.setattr(main_module, "load_flow_steps", lambda *_args, **_kwargs: [_sample_step("codex exec")])
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="codex",
                resolved_path=None,
                is_available=False,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 1
    assert "Fonte do fluxo: --flow-config (/tmp/flow.json)" in result.stdout
    assert "Diagnóstico de binários" in result.stdout
    assert "[MISSING]" in result.stdout
    assert "codex" in result.stdout
    assert "Não encontrado no PATH" in result.stdout
    assert "Ausentes: 1" in result.stdout
    assert "Pré-requisitos ausentes no PATH: codex." in result.stdout


def test_doctor_reports_success_when_all_prerequisites_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT),
    )
    monkeypatch.setattr(main_module, "load_flow_steps", lambda *_args, **_kwargs: [_sample_step("claude -p")])
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="claude",
                resolved_path="/usr/bin/claude",
                is_available=True,
                is_world_writable_location=False,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "Fonte do fluxo: default interno" in result.stdout
    assert "Diagnóstico de binários" in result.stdout
    assert "[OK]" in result.stdout
    assert "claude" in result.stdout
    assert "/usr/bin/claude" in result.stdout
    assert "Ausentes: 0" in result.stdout
    assert "Pré-requisitos atendidos." in result.stdout


def test_doctor_displays_models_and_effective_limits_per_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(main_module.MAX_OUTPUT_CHARS_ENV_VAR, "250000")
    monkeypatch.setenv(main_module.MAX_CONTEXT_CHARS_ENV_VAR, "90000")
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT),
    )
    monkeypatch.setattr(
        main_module,
        "load_flow_steps",
        lambda *_args, **_kwargs: [
            FlowStep(
                key="implement",
                agent_name="Codex",
                role_desc="Implementação",
                command="codex exec --model gpt-5-codex --skip-git-repo-check",
                instruction="instruction",
                max_input_chars=180000,
                max_context_chars=60000,
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="codex",
                resolved_path="/usr/bin/codex",
                is_available=True,
                is_world_writable_location=False,
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "_resolve_provider_rate_limits",
        lambda _steps: {
            "codex": ProviderRateLimitProbeResult(
                binary="codex",
                status="ok",
                summary="5h: 70% left; weekly: 27% left",
                entries=(),
                source="codex exec /status",
                model="gpt-5.3-codex",
            )
        },
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "Agentes e modelo" in result.stdout
    assert "Rate limits efetivos" in result.stdout
    assert "Cota (provedor)" in result.stdout
    assert "implement" in result.stdout
    assert "Codex" in result.stdout
    assert "180000 (passo)" in result.stdout
    assert "250000 (env)" in result.stdout
    assert "60000 (passo)" in result.stdout
    assert "5h: 70% left; weekly: 27% left" in result.stdout


def test_extract_model_from_command_supports_long_and_short_flags() -> None:
    assert (
        main_module._extract_model_from_command(
            "codex exec --model gpt-5-codex --skip-git-repo-check"
        )
        == "gpt-5-codex"
    )
    assert main_module._extract_model_from_command("gemini -m gemini-2.5-pro -p {input}") == "gemini-2.5-pro"
    assert main_module._extract_model_from_command("claude -p") == "padrão da CLI"


def test_doctor_uses_provider_model_when_command_uses_default_cli_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT),
    )
    monkeypatch.setattr(
        main_module,
        "load_flow_steps",
        lambda *_args, **_kwargs: [
            FlowStep(
                key="review",
                agent_name="Gemini",
                role_desc="Revisão",
                command="gemini -p {input}",
                instruction="instruction",
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="gemini",
                resolved_path="/usr/bin/gemini",
                is_available=True,
                is_world_writable_location=False,
            )
        ],
    )
    monkeypatch.setattr(
        main_module,
        "_resolve_provider_rate_limits",
        lambda _steps: {
            "gemini": ProviderRateLimitProbeResult(
                binary="gemini",
                status="unavailable",
                summary="sem indicador de cota restante via CLI; use /stats",
                entries=(),
                source="gemini -p /about",
                model="Auto (Gemini 3)",
            )
        },
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "Gemini" in result.stdout
    assert "Auto (Gemini 3) (CLI)" in result.stdout
    assert "sem indicador de cota" in result.stdout
    assert "/stats" in result.stdout


def test_doctor_emits_audit_logs_for_success(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, int, dict[str, object]]] = []

    monkeypatch.setattr(main_module, "get_audit_logger", lambda: object())
    monkeypatch.setattr(
        main_module,
        "log_event",
        lambda _logger, event, *, level=logging.INFO, **data: events.append((event, level, data)),
    )
    monkeypatch.setattr(
        main_module,
        "resolve_flow_config",
        lambda _: ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT),
    )
    monkeypatch.setattr(main_module, "load_flow_steps", lambda *_args, **_kwargs: [_sample_step("claude -p")])
    monkeypatch.setattr(
        main_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            main_module.BinaryPrerequisiteStatus(
                binary="claude",
                resolved_path="/usr/bin/claude",
                is_available=True,
                is_world_writable_location=False,
            )
        ],
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 0
    assert any(event == "main.doctor.invoked" for event, _level, _data in events)
    assert any(event == "main.doctor.success" for event, _level, _data in events)


def test_doctor_exits_when_logging_config_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_audit_logger",
        lambda: (_ for _ in ()).throw(ValueError("COUNCIL_LOG_LEVEL inválida")),
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["doctor"])

    assert result.exit_code == 1
    assert "Configuração inválida de logging" in result.stdout


def test_flow_sign_command_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow_path = tmp_path / "flow.json"
    private_key_path = tmp_path / "author.key.pem"
    expected_signature_path = tmp_path / "flow.json.sig"
    flow_path.write_text("[]", encoding="utf-8")
    private_key_path.write_text("private", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_sign(
        flow_path: Path,
        private_key_path: Path,
        key_id: str,
        *,
        signature_path: Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        captured["flow_path"] = flow_path
        captured["private_key_path"] = private_key_path
        captured["key_id"] = key_id
        captured["signature_path"] = signature_path
        captured["overwrite"] = overwrite
        return signature_path or expected_signature_path

    monkeypatch.setattr(main_module, "sign_flow_file", fake_sign)
    runner = CliRunner()

    result = runner.invoke(
        main_module.app,
        [
            "flow",
            "sign",
            str(flow_path),
            "--private-key",
            str(private_key_path),
            "--key-id",
            "author-v1",
            "--signature-file",
            str(expected_signature_path),
        ],
    )

    assert result.exit_code == 0
    assert captured["flow_path"] == flow_path
    assert captured["private_key_path"] == private_key_path
    assert captured["key_id"] == "author-v1"
    assert captured["signature_path"] == expected_signature_path
    assert "Assinatura criada" in result.stdout


def test_flow_verify_command_reports_error_on_invalid_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow_path = tmp_path / "flow.json"
    flow_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        main_module,
        "verify_flow_signature",
        lambda **_kwargs: (_ for _ in ()).throw(FlowSignatureError("assinatura inválida")),
    )
    runner = CliRunner()

    result = runner.invoke(main_module.app, ["flow", "verify", str(flow_path)])

    assert result.exit_code == 1
    assert "Falha na verificação da assinatura" in result.stdout


def test_flow_keygen_command_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    trusted_path = Path("/tmp/council/trusted_flow_keys/author-v1.pem")

    def fake_generate(
        private_key_path: Path,
        public_key_path: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        captured["private_key_path"] = private_key_path
        captured["public_key_path"] = public_key_path
        captured["overwrite"] = overwrite

    def fake_trust(
        public_key_path: Path,
        key_id: str,
        *,
        overwrite: bool = False,
    ) -> Path:
        captured["trust_public_key_path"] = public_key_path
        captured["trust_key_id"] = key_id
        captured["trust_overwrite"] = overwrite
        return trusted_path

    monkeypatch.setattr(main_module, "generate_flow_signing_keypair", fake_generate)
    monkeypatch.setattr(main_module, "trust_flow_public_key", fake_trust)
    runner = CliRunner()

    result = runner.invoke(
        main_module.app,
        ["flow", "keygen", "--key-id", "author-v1", "--trust"],
    )

    assert result.exit_code == 0
    assert captured["private_key_path"] == Path("author-v1.key.pem")
    assert captured["public_key_path"] == Path("author-v1.pub.pem")
    assert captured["trust_public_key_path"] == Path("author-v1.pub.pem")
    assert captured["trust_key_id"] == "author-v1"
    assert "Chave privada gerada em" in result.stdout
    assert "Chave pública confiada em" in result.stdout


def test_flow_trust_command_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_key_path = tmp_path / "author.pub.pem"
    public_key_path.write_text("fake", encoding="utf-8")
    captured: dict[str, object] = {}
    trusted_path = Path("/tmp/council/trusted_flow_keys/author-v1.pem")

    def fake_trust(
        public_key_path: Path,
        key_id: str,
        *,
        overwrite: bool = False,
    ) -> Path:
        captured["public_key_path"] = public_key_path
        captured["key_id"] = key_id
        captured["overwrite"] = overwrite
        return trusted_path

    monkeypatch.setattr(main_module, "trust_flow_public_key", fake_trust)
    runner = CliRunner()

    result = runner.invoke(
        main_module.app,
        ["flow", "trust", str(public_key_path), "--key-id", "author-v1"],
    )

    assert result.exit_code == 0
    assert captured["public_key_path"] == public_key_path
    assert captured["key_id"] == "author-v1"
    assert captured["overwrite"] is False
    assert "Chave confiada com sucesso" in result.stdout
