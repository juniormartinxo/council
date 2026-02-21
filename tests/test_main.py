from pathlib import Path

import council.main as main_module
from council.config import (
    FLOW_CONFIG_SOURCE_CLI,
    FLOW_CONFIG_SOURCE_CWD,
    FLOW_CONFIG_SOURCE_DEFAULT,
    FLOW_CONFIG_SOURCE_ENV,
    FLOW_CONFIG_SOURCE_USER,
    ResolvedFlowConfig,
)


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
