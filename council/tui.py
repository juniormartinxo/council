from __future__ import annotations

import threading
from contextlib import contextmanager

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from council.config import ConfigError, load_flow_steps
from council.executor import Executor
from council.orchestrator import Orchestrator
from council.state import CouncilState


class _ConsoleProxy:
    """Proxy mínimo para suportar ui.console.print() no Orchestrator."""

    def __init__(self, app: "CouncilTextualApp"):
        self._app = app

    def print(self, *objects, sep: str = " ", end: str = "\n", **_: object) -> None:
        text = sep.join(str(obj) for obj in objects)
        if end and end != "\n":
            text += end
        self._app.append_stream(text.rstrip("\n"))


class TextualUIAdapter:
    """
    Adaptador para reaproveitar o core existente (Executor/Orchestrator)
    dentro da UI Textual.
    """

    def __init__(self, app: "CouncilTextualApp"):
        self.app = app
        self.console = _ConsoleProxy(app)

    @contextmanager
    def spinner(self, text: str):
        self.app.set_status(text, style="yellow")
        try:
            yield
        finally:
            self.app.set_status("Pronto.", style="green")

    @contextmanager
    def live_stream(self, title: str, style: str = "blue", max_height: int = 10):
        del max_height
        self.app.start_stream(title, style=style)

        def update_content(new_text: str):
            self.app.append_stream(new_text)

        try:
            yield update_content
        finally:
            self.app.finish_stream()

    def show_panel(
        self,
        title: str,
        content: str,
        style: str = "blue",
        is_code: bool = False,
        language: str = "python",
    ) -> None:
        self.app.add_result_panel(
            title=title,
            content=content,
            style=style,
            is_code=is_code,
            language=language,
        )

    def show_error(self, message: str) -> None:
        self.app.show_error(message)

    def show_success(self, message: str) -> None:
        self.app.show_success(message)


class CouncilTextualApp(App[None]):
    TITLE = "Council TUI"
    SUB_TITLE = "Orquestrador Multi-Agent"
    RUN_LABEL_IDLE = "Executar"
    RUN_LABEL_RUNNING = "Executando..."
    BINDINGS = [
        ("ctrl+r", "run_flow", "Executar"),
        ("ctrl+l", "clear_logs", "Limpar Logs"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    #controls {
        height: auto;
        padding: 1;
        border: round $accent;
    }

    .label {
        margin: 0 0 1 0;
        text-style: bold;
    }

    #flow_row {
        height: auto;
        margin-top: 1;
    }

    #flow_input {
        width: 1fr;
        margin-right: 1;
    }

    #run_button {
        margin-right: 1;
    }

    #logs {
        height: 1fr;
        padding: 0 1 1 1;
    }

    .log_col {
        width: 1fr;
        height: 1fr;
        margin-right: 1;
    }

    .log_col:last-child {
        margin-right: 0;
    }

    RichLog {
        border: round #666666;
        height: 1fr;
    }

    #status {
        height: 3;
        padding: 0 1;
        background: #202124;
        content-align: left middle;
    }
    """

    def __init__(self, initial_prompt: str = "", initial_flow_config: str = ""):
        super().__init__()
        self._initial_prompt = initial_prompt
        self._initial_flow_config = initial_flow_config
        self._flow_running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="controls"):
            yield Static("Prompt do usuário", classes="label")
            yield Input(
                value=self._initial_prompt,
                placeholder="Digite o requisito para iniciar o fluxo...",
                id="prompt_input",
            )

            with Horizontal(id="flow_row"):
                yield Input(
                    value=self._initial_flow_config,
                    placeholder="flow.example.json (opcional)",
                    id="flow_input",
                )
                yield Button(self.RUN_LABEL_IDLE, id="run_button", variant="primary")
                yield Button("Limpar", id="clear_button")

        with Horizontal(id="logs"):
            with Vertical(classes="log_col"):
                yield Static("Stream em tempo real", classes="label")
                yield RichLog(id="stream_log", wrap=True, markup=False, highlight=False)

            with Vertical(classes="log_col"):
                yield Static("Resultados por etapa", classes="label")
                yield RichLog(id="result_log", wrap=True, markup=False, highlight=True)

        yield Static("Pronto.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt_input", Input).focus()

    def _dispatch_ui(self, callback, *args) -> None:
        if threading.current_thread() is threading.main_thread():
            callback(*args)
        else:
            self.call_from_thread(callback, *args)

    def _stream_log(self) -> RichLog:
        return self.query_one("#stream_log", RichLog)

    def _result_log(self) -> RichLog:
        return self.query_one("#result_log", RichLog)

    def set_status(self, text: str, style: str = "white") -> None:
        self._dispatch_ui(self._set_status, text, style)

    def _set_status(self, text: str, style: str) -> None:
        self.query_one("#status", Static).update(Text(text, style=style))

    def start_stream(self, title: str, style: str = "blue") -> None:
        self._dispatch_ui(self._start_stream, title, style)

    def _start_stream(self, title: str, style: str) -> None:
        self._stream_log().write(Text(f"== {title} ==", style=f"bold {style}"))
        self.set_status(f"Executando: {title}", style="yellow")

    def append_stream(self, text: str) -> None:
        self._dispatch_ui(self._append_stream, text)

    def _append_stream(self, text: str) -> None:
        if text:
            self._stream_log().write(text)

    def finish_stream(self) -> None:
        self._dispatch_ui(self._finish_stream)

    def _finish_stream(self) -> None:
        self._stream_log().write(Text("----------------------------------------", style="dim"))

    def add_result_panel(
        self,
        title: str,
        content: str,
        style: str = "blue",
        is_code: bool = False,
        language: str = "python",
    ) -> None:
        self._dispatch_ui(self._add_result_panel, title, content, style, is_code, language)

    def _add_result_panel(
        self,
        title: str,
        content: str,
        style: str,
        is_code: bool,
        language: str,
    ) -> None:
        renderable = (
            Syntax(content, language, theme="monokai", word_wrap=True) if is_code else content
        )
        panel = Panel(
            renderable,
            title=Text(title, style=f"bold {style}"),
            border_style=style,
            expand=False,
        )
        self._result_log().write(panel)

    def show_error(self, message: str) -> None:
        self._dispatch_ui(self._show_error, message)

    def _show_error(self, message: str) -> None:
        self._result_log().write(
            Panel(
                message,
                title=Text("Erro", style="bold red"),
                border_style="red",
                expand=False,
            )
        )
        self._set_status(message, style="red")

    def show_success(self, message: str) -> None:
        self._dispatch_ui(self._show_success, message)

    def _show_success(self, message: str) -> None:
        self._result_log().write(
            Panel(
                message,
                title=Text("Sucesso", style="bold green"),
                border_style="green",
                expand=False,
            )
        )
        self._set_status(message, style="green")

    def clear_logs(self) -> None:
        self._dispatch_ui(self._clear_logs)

    def _clear_logs(self) -> None:
        self._stream_log().clear()
        self._result_log().clear()
        self._set_status("Pronto.", style="white")

    def _set_running(self, running: bool) -> None:
        self._flow_running = running
        run_button = self.query_one("#run_button", Button)
        run_button.disabled = running
        run_button.label = self.RUN_LABEL_RUNNING if running else self.RUN_LABEL_IDLE
        self.query_one("#prompt_input", Input).disabled = running
        self.query_one("#flow_input", Input).disabled = running

    def _start_execution(self) -> None:
        if self._flow_running:
            return

        prompt = self.query_one("#prompt_input", Input).value.strip()
        if not prompt:
            self._set_status("Informe um prompt antes de executar.", style="red")
            return

        flow_path = self.query_one("#flow_input", Input).value.strip() or None

        self.clear_logs()
        self._set_running(True)
        self._set_status("Preparando execução...", style="yellow")
        thread = threading.Thread(
            target=self.run_council_flow,
            args=(prompt, flow_path),
            daemon=True,
        )
        thread.start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run_button":
            self._start_execution()
        elif event.button.id == "clear_button":
            self.clear_logs()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"prompt_input", "flow_input"}:
            self._start_execution()

    def action_run_flow(self) -> None:
        self._start_execution()

    def action_clear_logs(self) -> None:
        self.clear_logs()

    def run_council_flow(self, prompt: str, flow_config: str | None) -> None:
        ui = TextualUIAdapter(self)
        state = CouncilState()
        executor = Executor(ui)

        try:
            flow_steps = load_flow_steps(flow_config)
        except ConfigError as exc:
            ui.show_error(f"Erro ao carregar configuração do fluxo: {exc}")
            self._dispatch_ui(self._set_running, False)
            return

        orchestrator = Orchestrator(state, executor, ui, flow_steps=flow_steps)

        try:
            orchestrator.run_flow(prompt)
        except Exception as exc:
            ui.show_error(f"Erro inesperado na execução: {exc}")
        finally:
            self._dispatch_ui(self._set_running, False)


def run_tui(initial_prompt: str = "", initial_flow_config: str = "") -> None:
    CouncilTextualApp(
        initial_prompt=initial_prompt,
        initial_flow_config=initial_flow_config,
    ).run()
