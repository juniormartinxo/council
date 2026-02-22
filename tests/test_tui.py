import json
import os
import stat
from pathlib import Path

import pytest

import council.tui as tui_module
from council.config import FLOW_CONFIG_SOURCE_CWD, FLOW_CONFIG_SOURCE_ENV, FlowStep, ResolvedFlowConfig
from council.paths import COUNCIL_HOME_ENV_VAR
from council.prerequisites import BinaryPrerequisiteStatus
from council.tui import CouncilTextualApp
from council.tui_state import TUI_STATE_PASSPHRASE_ENV_VAR


def _build_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CouncilTextualApp, Path]:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.setattr(CouncilTextualApp, "STATE_FILE_PATH", council_home / "tui_state.json")
    return CouncilTextualApp(), council_home


def test_save_clipboard_fallback_uses_council_home_and_secure_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)

    saved_path, directory_secured = app._save_clipboard_fallback(
        payload="segredo",
        safe_label="stream_geral",
    )

    assert saved_path.parent == council_home / app.CLIPBOARD_FALLBACK_DIR_NAME
    assert saved_path.read_text(encoding="utf-8") == "segredo"
    assert directory_secured is True
    assert stat.S_IMODE(saved_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved_path.parent.stat().st_mode) == 0o700
    assert saved_path.name.startswith("council_stream_geral_")


def test_cleanup_clipboard_fallback_files_removes_only_expired_prefixed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)
    clipboard_dir = council_home / app.CLIPBOARD_FALLBACK_DIR_NAME
    clipboard_dir.mkdir(parents=True, exist_ok=True)

    expired_file = clipboard_dir / "council_expired.txt"
    fresh_file = clipboard_dir / "council_fresh.txt"
    unrelated_file = clipboard_dir / "notes.txt"
    for path in (expired_file, fresh_file, unrelated_file):
        path.write_text("payload", encoding="utf-8")

    os.utime(expired_file, (10, 10))
    os.utime(fresh_file, (95, 95))
    os.utime(unrelated_file, (10, 10))

    monkeypatch.setattr(app, "CLIPBOARD_FALLBACK_RETENTION_SECONDS", 10)

    app._cleanup_clipboard_fallback_files(clipboard_dir, now=100)

    assert not expired_file.exists()
    assert fresh_file.exists()
    assert unrelated_file.exists()


def test_copy_text_payload_persists_fallback_when_clipboard_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    def failing_copy(_: str) -> None:
        raise RuntimeError("clipboard unavailable")

    monkeypatch.setattr(app, "copy_to_clipboard", failing_copy)
    monkeypatch.setattr(
        app,
        "set_status",
        lambda message, style="": statuses.append((message, style)),
    )

    app._copy_text_payload(payload="conteudo sensivel", label="stream_geral", empty_message="vazio")

    fallback_files = list((council_home / app.CLIPBOARD_FALLBACK_DIR_NAME).glob("council_*.txt"))

    assert len(fallback_files) == 1
    assert fallback_files[0].read_text(encoding="utf-8") == "conteudo sensivel"
    assert statuses[-1][1] == "yellow"
    assert "Clipboard indisponível. Conteúdo salvo em" in statuses[-1][0]


def test_copy_text_payload_warns_when_directory_permissions_cannot_be_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    def failing_copy(_: str) -> None:
        raise RuntimeError("no clipboard")

    monkeypatch.setattr(app, "copy_to_clipboard", failing_copy)
    monkeypatch.setattr(
        app,
        "set_status",
        lambda message, style="": statuses.append((message, style)),
    )
    monkeypatch.setattr(app, "_secure_directory_permissions", lambda _: False)
    app._copy_text_payload(payload="conteudo sensivel", label="stream_geral", empty_message="vazio")

    assert statuses[-1][1] == "yellow"
    assert "aviso: permissões do diretório não puderam ser restritas" in statuses[-1][0]


def test_confirm_implicit_flow_requires_double_confirmation_in_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []
    flow_path = tmp_path / "flow.json"

    monkeypatch.setattr(app, "_set_status", lambda message, style: statuses.append((message, style)))
    resolved_flow = ResolvedFlowConfig(path=flow_path, source=FLOW_CONFIG_SOURCE_CWD)

    first_attempt = app._confirm_implicit_flow_if_needed(resolved_flow, flow_path=None)
    second_attempt = app._confirm_implicit_flow_if_needed(resolved_flow, flow_path=None)

    assert first_attempt is False
    assert second_attempt is True
    assert statuses[-1][1] == "yellow"
    assert "Detectada configuração implícita via ./flow.json." in statuses[-1][0]
    assert app._normalize_path_key(flow_path) in app._trusted_auto_flow_paths


def test_confirm_implicit_flow_skips_confirmation_when_flow_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []
    flow_path = tmp_path / "flow.json"

    monkeypatch.setattr(app, "_set_status", lambda message, style: statuses.append((message, style)))
    resolved_flow = ResolvedFlowConfig(path=flow_path, source=FLOW_CONFIG_SOURCE_CWD)

    is_allowed = app._confirm_implicit_flow_if_needed(resolved_flow, flow_path=str(flow_path))

    assert is_allowed is True
    assert statuses == []


def test_confirm_implicit_flow_for_env_source_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []
    flow_path = tmp_path / "flow.json"

    monkeypatch.setattr(app, "_set_status", lambda message, style: statuses.append((message, style)))
    resolved_flow = ResolvedFlowConfig(path=flow_path, source=FLOW_CONFIG_SOURCE_ENV)

    first_attempt = app._confirm_implicit_flow_if_needed(resolved_flow, flow_path=None)
    second_attempt = app._confirm_implicit_flow_if_needed(resolved_flow, flow_path=None)

    assert first_attempt is False
    assert second_attempt is True
    assert statuses[-1][1] == "yellow"
    assert "COUNCIL_FLOW_CONFIG" in statuses[-1][0]


def test_run_council_flow_handles_invalid_runtime_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    monkeypatch.setattr(
        tui_module,
        "CouncilState",
        lambda: (_ for _ in ()).throw(ValueError("bad limit")),
    )
    monkeypatch.setattr(app, "show_error", lambda message: statuses.append((message, "red")))
    monkeypatch.setattr(app, "_dispatch_ui", lambda callback, *args: callback(*args))
    monkeypatch.setattr(app, "_set_running", lambda running: statuses.append((f"running={running}", "state")))

    app.run_council_flow(prompt="prompt", flow_config=None)

    assert any("Configuração inválida de limites" in message for message, _ in statuses)
    assert ("running=False", "state") in statuses


def test_run_council_flow_blocks_when_prerequisites_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    step = FlowStep(
        key="step",
        agent_name="Agent",
        role_desc="Role",
        command="codex exec --skip-git-repo-check",
        instruction="instruction",
    )

    monkeypatch.setattr(tui_module, "CouncilState", lambda: object())
    monkeypatch.setattr(tui_module, "Executor", lambda _ui: object())
    monkeypatch.setattr(tui_module, "load_flow_steps", lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(
        tui_module,
        "evaluate_flow_prerequisites",
        lambda _steps: [
            BinaryPrerequisiteStatus(
                binary="codex",
                resolved_path=None,
                is_available=False,
            )
        ],
    )
    monkeypatch.setattr(app, "show_error", lambda message: statuses.append((message, "red")))
    monkeypatch.setattr(app, "_dispatch_ui", lambda callback, *args: callback(*args))
    monkeypatch.setattr(app, "_set_running", lambda running: statuses.append((f"running={running}", "state")))

    app.run_council_flow(prompt="prompt", flow_config=None)

    assert any("Pré-requisitos ausentes no PATH" in message for message, _ in statuses)
    assert ("running=False", "state") in statuses


def test_persist_state_with_passphrase_does_not_store_sensitive_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)
    monkeypatch.setenv(TUI_STATE_PASSPHRASE_ENV_VAR, "senha-super-secreta")
    state_path = council_home / "tui_state.json"

    app._prompt_history = ["senha de produção"]
    app._persist_state(last_prompt="token secreto", last_flow_config="flow.example.json")

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted.get("last_prompt") != "token secreto"
    assert persisted.get("prompt_history") != ["senha de produção"]
    assert persisted["last_flow_config"] == "flow.example.json"
    assert (
        "encrypted_prompt_state" in persisted
        or persisted.get("prompt_history") == []
    )


def test_load_persisted_state_preserves_last_flow_config_when_decryption_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    council_home = tmp_path / ".council-home"
    state_path = council_home / "tui_state.json"
    council_home.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "last_flow_config": "flow.secreto.json",
                "encrypted_prompt_state": {
                    "version": 1,
                    "kdf": "pbkdf2-sha256",
                    "iterations": 390000,
                    "salt": "invalid",
                    "token": "invalid",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.setenv(TUI_STATE_PASSPHRASE_ENV_VAR, "senha")
    monkeypatch.setattr(CouncilTextualApp, "STATE_FILE_PATH", state_path)

    app = CouncilTextualApp()

    assert app._initial_flow_config == "flow.secreto.json"
    assert app._prompt_history == []
    assert app._state_warning is not None
