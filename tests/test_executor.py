import subprocess
from typing import Any

import pytest

import council.executor as executor_module
from council.executor import (
    CLI_INPUT_BLOCK_END,
    CLI_INPUT_BLOCK_START,
    CommandError,
    ExecutionAborted,
    Executor,
)


class DummyUI:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class FakeStdin:
    def __init__(self) -> None:
        self.written = ""
        self.closed = False

    def write(self, text: str) -> None:
        self.written += text

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)
        self.closed = False

    def readline(self) -> str:
        try:
            return next(self._lines)
        except StopIteration:
            return ""

    def close(self) -> None:
        self.closed = True


class FakeStderr:
    def __init__(self, content: str) -> None:
        self._content = content
        self.closed = False

    def read(self) -> str:
        return self._content

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(
        self,
        stdout_lines: list[str] | None = None,
        stderr_content: str = "",
        wait_result: int = 0,
        wait_exception: Exception | None = None,
    ) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(stdout_lines or [])
        self.stderr = FakeStderr(stderr_content)
        self._wait_result = wait_result
        self._wait_exception = wait_exception
        self.pid = 1234

    def wait(self, timeout: int | None = None) -> int:
        if self._wait_exception is not None:
            raise self._wait_exception
        return self._wait_result

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def _patch_popen(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls["command"] = command
        calls["kwargs"] = kwargs
        return process

    monkeypatch.setattr(executor_module.subprocess, "Popen", fake_popen)
    return calls


def test_prepare_command_injects_placeholder_and_disables_stdin() -> None:
    executor = Executor(DummyUI())

    command_to_run, stdin_payload = executor._prepare_command("gemini -p {input}", "a 'quoted' prompt")

    assert command_to_run == [
        "gemini",
        "-p",
        (
            f"{CLI_INPUT_BLOCK_START}\n"
            "PROMPT INTEGRAL ENVIADO VIA ARGV.\n"
            "a 'quoted' prompt\n"
            f"{CLI_INPUT_BLOCK_END}"
        ),
    ]
    assert stdin_payload == ""


def test_prepare_command_keeps_stdin_when_no_placeholder_for_other_tools() -> None:
    executor = Executor(DummyUI())

    command_to_run, stdin_payload = executor._prepare_command("claude -p", "hello")

    assert command_to_run == ["claude", "-p"]
    assert stdin_payload == "hello"


def test_prepare_command_auto_injects_gemini_prompt_when_missing_value() -> None:
    executor = Executor(DummyUI())

    command_to_run, stdin_payload = executor._prepare_command("gemini -p", "hello world")

    assert command_to_run == [
        "gemini",
        "-p",
        (
            f"{CLI_INPUT_BLOCK_START}\n"
            "PROMPT INTEGRAL ENVIADO VIA ARGV.\n"
            "hello world\n"
            f"{CLI_INPUT_BLOCK_END}"
        ),
    ]
    assert stdin_payload == ""


def test_prepare_command_keeps_empty_argv_payload_as_empty_string() -> None:
    executor = Executor(DummyUI())

    command_to_run, stdin_payload = executor._prepare_command("gemini -p {input}", "   ")

    assert command_to_run == ["gemini", "-p", ""]
    assert stdin_payload == ""


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("gemini -p", True),
        ("gemini -p texto", False),
        ("gemini --prompt", True),
        ("gemini --prompt texto", False),
        ("gemini --prompt=texto", False),
        ("/usr/local/bin/gemini -p", True),
        ("claude -p", False),
        ("gemini", False),
        ("gemini 'unterminated", False),
    ],
)
def test_is_gemini_prompt_missing_value(command: str, expected: bool) -> None:
    executor = Executor(DummyUI())

    assert executor._is_gemini_prompt_missing_value(command) is expected


def test_run_cli_returns_output_and_streams_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)
    process = FakeProcess(stdout_lines=["line 1\n", "line 2\n"])
    calls = _patch_popen(monkeypatch, process)
    streamed: list[str] = []

    output = executor.run_cli("claude -p", "prompt", on_output=streamed.append)

    assert output == "line 1\nline 2"
    assert process.stdin.written == "prompt"
    assert process.stdin.closed is True
    assert streamed == ["line 1", "line 2"]
    assert ui.errors == []
    assert calls["command"] == ["claude", "-p"]
    assert calls["kwargs"]["shell"] is False


def test_run_cli_raises_command_error_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)
    process = FakeProcess(stderr_content="boom", wait_result=2)
    _patch_popen(monkeypatch, process)

    with pytest.raises(CommandError, match="Erro no comando"):
        executor.run_cli("tool --flag", "payload")

    assert len(ui.errors) == 1
    assert "Código 2" in ui.errors[0]


def test_run_cli_raises_timeout_and_terminates_process(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)
    process = FakeProcess(wait_exception=subprocess.TimeoutExpired(cmd="tool", timeout=3))
    _patch_popen(monkeypatch, process)
    terminated = {"called": False}

    def fake_terminate(_: FakeProcess) -> None:
        terminated["called"] = True

    monkeypatch.setattr(executor, "_terminate_process", fake_terminate)

    with pytest.raises(CommandError, match="Timeout no comando"):
        executor.run_cli("tool", "payload", timeout=3)

    assert terminated["called"] is True
    assert len(ui.errors) == 1
    assert "timeout" in ui.errors[0].lower()


def test_run_cli_clears_previous_cancel_request(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)
    executor.request_cancel()
    process = FakeProcess(stdout_lines=["ok\n"])
    _patch_popen(monkeypatch, process)

    output = executor.run_cli("tool", "payload")
    assert output == "ok"
    assert ui.errors == []


def test_run_cli_aborts_when_cancel_requested_during_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = DummyUI()
    executor = Executor(ui)
    process = FakeProcess(stdout_lines=["line 1\n", "line 2\n"])
    _patch_popen(monkeypatch, process)
    terminated = {"called": False}

    def fake_terminate(_: FakeProcess) -> None:
        terminated["called"] = True

    monkeypatch.setattr(executor, "_terminate_process", fake_terminate)

    def cancel_on_first_line(_: str) -> None:
        executor.request_cancel()

    with pytest.raises(ExecutionAborted):
        executor.run_cli("tool", "payload", on_output=cancel_on_first_line)

    assert terminated["called"] is True


def test_run_cli_rejects_input_above_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui, max_input_chars=4)

    def popen_should_not_run(*args: Any, **kwargs: Any) -> FakeProcess:
        raise AssertionError("Popen should not be called when input is above limit.")

    monkeypatch.setattr(executor_module.subprocess, "Popen", popen_should_not_run)

    with pytest.raises(CommandError, match="Input acima do limite"):
        executor.run_cli("tool", "12345")

    assert len(ui.errors) == 1
    assert "limite configurado" in ui.errors[0]


def test_run_cli_truncates_output_above_configured_limit_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = DummyUI()
    executor = Executor(ui, max_output_chars=6)
    process = FakeProcess(stdout_lines=["1234\n", "5678\n"])
    _patch_popen(monkeypatch, process)

    output = executor.run_cli("tool", "payload")

    assert "saída truncada" in output
    assert output.endswith("5678")
    assert ui.errors == []


def test_run_cli_allows_per_call_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui, max_input_chars=10, max_output_chars=10)

    def popen_should_not_run(*args: Any, **kwargs: Any) -> FakeProcess:
        raise AssertionError("Popen should not be called when per-call input limit is exceeded.")

    monkeypatch.setattr(executor_module.subprocess, "Popen", popen_should_not_run)

    with pytest.raises(CommandError, match="Input acima do limite"):
        executor.run_cli("tool", "12345", max_input_chars=4)


def test_run_cli_rejects_invalid_per_call_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)

    def popen_should_not_run(*args: Any, **kwargs: Any) -> FakeProcess:
        raise AssertionError("Popen should not be called when limits are invalid.")

    monkeypatch.setattr(executor_module.subprocess, "Popen", popen_should_not_run)

    with pytest.raises(CommandError, match="max_input_chars inválido"):
        executor.run_cli("tool", "payload", max_input_chars=0)

    with pytest.raises(CommandError, match="max_output_chars inválido"):
        executor.run_cli("tool", "payload", max_output_chars=0)


def test_executor_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="max_input_chars"):
        Executor(DummyUI(), max_input_chars=0)

    with pytest.raises(ValueError, match="max_output_chars"):
        Executor(DummyUI(), max_output_chars=0)


def test_executor_rejects_invalid_limit_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUNCIL_MAX_INPUT_CHARS", "0")
    with pytest.raises(ValueError, match="COUNCIL_MAX_INPUT_CHARS"):
        Executor(DummyUI())

    monkeypatch.delenv("COUNCIL_MAX_INPUT_CHARS", raising=False)
    monkeypatch.setenv("COUNCIL_MAX_OUTPUT_CHARS", "abc")
    with pytest.raises(ValueError, match="COUNCIL_MAX_OUTPUT_CHARS"):
        Executor(DummyUI())


def test_run_cli_rejects_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = DummyUI()
    executor = Executor(ui)

    def popen_should_not_run(*args: Any, **kwargs: Any) -> FakeProcess:
        raise AssertionError("Popen should not be called when timeout is invalid.")

    monkeypatch.setattr(executor_module.subprocess, "Popen", popen_should_not_run)

    with pytest.raises(CommandError, match="Timeout inválido"):
        executor.run_cli("tool", "payload", timeout=0)
