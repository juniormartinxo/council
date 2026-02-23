from __future__ import annotations

import os
import tempfile
import threading
import time
import logging
from contextlib import contextmanager
from pathlib import Path

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static, Tab, Tabs

from council.config import (
    ConfigError,
    FLOW_CONFIG_SOURCE_CWD,
    FLOW_CONFIG_SOURCE_ENV,
    ResolvedFlowConfig,
    load_flow_steps,
    resolve_flow_config,
)
from council.audit_log import get_audit_logger, log_event
from council.executor import CommandError, ExecutionAborted, Executor
from council.history_store import HistoryStore
from council.orchestrator import Orchestrator
from council.paths import get_council_home, get_tui_state_file_path
from council.prerequisites import (
    evaluate_flow_prerequisites,
    find_missing_binaries,
    find_world_writable_binary_locations,
)
from council.state import CouncilState
from council.tui_state import (
    TUIStateCryptoError,
    TUIStateCryptoUnavailableError,
    load_tui_state_payload,
    persist_tui_state_payload,
    read_raw_tui_state_payload,
    read_tui_state_passphrase,
)


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

    def request_step_feedback(self, agent_name: str, role_desc: str, output: str) -> str | None:
        return self.app.request_step_feedback(agent_name=agent_name, role_desc=role_desc, output=output)

    def set_active_step(self, step_key: str, label: str) -> None:
        self.app.set_active_step(step_key=step_key, label=label)


class CouncilTextualApp(App[None]):
    TITLE = "Council TUI"
    SUB_TITLE = "Orquestrador Multi-Agent"
    STATE_FILE_PATH = get_tui_state_file_path()
    MAX_HISTORY_ITEMS = 200
    GENERAL_STEP_ID = "__general__"
    RUN_LABEL_IDLE = "Executar"
    RUN_LABEL_RUNNING = "Executando..."
    CLIPBOARD_FALLBACK_DIR_NAME = "clipboard"
    CLIPBOARD_FALLBACK_FILE_PREFIX = "council_"
    CLIPBOARD_FALLBACK_RETENTION_SECONDS = 60 * 60 * 24 * 7
    BINDINGS = [
        ("ctrl+q", "quit_app", "Fechar"),
        ("ctrl+r", "run_flow", "Executar"),
        ("ctrl+x", "abort_flow", "Abortar"),
        ("ctrl+l", "clear_logs", "Limpar Logs"),
        ("ctrl+1", "copy_stream", "Copiar Stream"),
        ("ctrl+2", "copy_results", "Copiar Resultados"),
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
        margin: 0;
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

    .log_header {
        height: auto;
        margin: 0 0 1 0;
    }

    .copy_btn {
        margin-left: 1;
    }

    #stream_tabs,
    #result_tabs {
        margin: 0 0 1 0;
    }

    #status {
        height: 3;
        padding: 0 1;
        background: #202124;
        content-align: left middle;
    }

    #feedback_row {
        height: auto;
        padding: 0 1 1 1;
    }

    #feedback_input {
        width: 1fr;
        margin-right: 1;
    }

    #feedback_hint {
        width: 1fr;
        margin-right: 1;
    }
    """

    def __init__(self, initial_prompt: str = "", initial_flow_config: str = ""):
        super().__init__()
        self._state_warning: str | None = None
        persisted_state = self._load_persisted_state()
        saved_flow_config = self._coerce_string(persisted_state.get("last_flow_config"))

        # O campo de prompt abre vazio por padrão; histórico continua disponível via seta ↑/↓.
        self._initial_prompt = initial_prompt
        self._initial_flow_config = initial_flow_config or saved_flow_config
        self._last_prompt_value = self._initial_prompt
        self._last_flow_config_value = self._initial_flow_config
        self._flow_running = False
        self._awaiting_feedback = False
        self._feedback_event = threading.Event()
        self._feedback_value: str | None = None
        self._stream_buffers: dict[str, list[str]] = {self.GENERAL_STEP_ID: []}
        self._result_render_buffers: dict[str, list[object]] = {self.GENERAL_STEP_ID: []}
        self._result_text_buffers: dict[str, list[str]] = {self.GENERAL_STEP_ID: []}
        self._step_labels: dict[str, str] = {self.GENERAL_STEP_ID: "Geral"}
        self._stream_selected_step_id = self.GENERAL_STEP_ID
        self._result_selected_step_id = self.GENERAL_STEP_ID
        self._current_step_id = self.GENERAL_STEP_ID
        self._executor_lock = threading.Lock()
        self._active_executor: Executor | None = None
        self._audit_logger = get_audit_logger()
        self._trusted_auto_flow_paths: set[str] = set()
        self._pending_auto_flow_confirmation: str | None = None
        self._prompt_history = self._normalize_prompt_history(persisted_state.get("prompt_history"))
        self._history_index = len(self._prompt_history)
        self._history_draft = ""

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
                with Horizontal(classes="log_header"):
                    yield Static("Stream em tempo real", classes="label")
                    yield Button("Copiar", id="copy_stream_button", classes="copy_btn")
                yield Tabs(Tab("Geral", id="stream_tab__general"), id="stream_tabs")
                yield RichLog(id="stream_log", wrap=True, markup=False, highlight=False)

            with Vertical(classes="log_col"):
                with Horizontal(classes="log_header"):
                    yield Static("Resultados por etapa", classes="label")
                    yield Button("Copiar", id="copy_results_button", classes="copy_btn")
                yield Tabs(Tab("Geral", id="result_tab__general"), id="result_tabs")
                yield RichLog(id="result_log", wrap=True, markup=False, highlight=True)

        with Horizontal(id="feedback_row"):
            yield Static(
                "Checkpoint por etapa: aguarde a saída do agente para Continuar ou Enviar ajuste.",
                id="feedback_hint",
            )
            yield Input(
                placeholder="Escreva um ajuste para o agente atual e pressione Enter",
                id="feedback_input",
                disabled=True,
            )
            yield Button("Continuar", id="continue_button", disabled=True)
            yield Button("Enviar ajuste", id="send_feedback_button", disabled=True, variant="primary")
            yield Button("Abortar", id="abort_button", disabled=True)

        yield Static("Pronto.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt_input", Input).focus()
        if self._state_warning:
            self.set_status(self._state_warning, style="yellow")

    def on_unmount(self) -> None:
        self._persist_state()

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down"}:
            return
        if self._flow_running or self._awaiting_feedback:
            return

        focused = self.focused
        if not isinstance(focused, Input) or focused.id != "prompt_input":
            return

        if event.key == "up":
            self._navigate_prompt_history_up()
        else:
            self._navigate_prompt_history_down()

        event.stop()
        event.prevent_default()

    def _dispatch_ui(self, callback, *args) -> None:
        if threading.current_thread() is threading.main_thread():
            callback(*args)
        else:
            self.call_from_thread(callback, *args)

    def _stream_log(self) -> RichLog:
        return self.query_one("#stream_log", RichLog)

    def _result_log(self) -> RichLog:
        return self.query_one("#result_log", RichLog)

    def _stream_tabs(self) -> Tabs:
        return self.query_one("#stream_tabs", Tabs)

    def _result_tabs(self) -> Tabs:
        return self.query_one("#result_tabs", Tabs)

    def _tab_id(self, prefix: str, step_id: str) -> str:
        if step_id == self.GENERAL_STEP_ID:
            return f"{prefix}__general"
        return f"{prefix}__{step_id}"

    def _step_from_tab_id(self, tab_id: str, prefix: str) -> str:
        prefix_with_sep = f"{prefix}__"
        if not tab_id.startswith(prefix_with_sep):
            return self.GENERAL_STEP_ID
        raw_step = tab_id[len(prefix_with_sep) :]
        return self.GENERAL_STEP_ID if raw_step == "general" else raw_step

    def set_active_step(self, step_key: str, label: str) -> None:
        self._dispatch_ui(self._set_active_step, step_key, label)

    def _set_active_step(self, step_key: str, label: str) -> None:
        normalized_step = step_key.strip() or self.GENERAL_STEP_ID
        self._current_step_id = normalized_step
        self._step_labels[normalized_step] = label
        self._ensure_step_tabs(normalized_step, label)

        self._stream_tabs().active = self._tab_id("stream_tab", normalized_step)
        self._result_tabs().active = self._tab_id("result_tab", normalized_step)

    def _ensure_step_tabs(self, step_id: str, label: str) -> None:
        if step_id not in self._stream_buffers:
            self._stream_buffers[step_id] = []
        if step_id not in self._result_render_buffers:
            self._result_render_buffers[step_id] = []
        if step_id not in self._result_text_buffers:
            self._result_text_buffers[step_id] = []

        stream_tab_ids = {tab.id for tab in self._stream_tabs().query("Tab")}
        stream_tab_id = self._tab_id("stream_tab", step_id)
        if stream_tab_id not in stream_tab_ids:
            self._stream_tabs().add_tab(Tab(label, id=stream_tab_id))

        result_tab_ids = {tab.id for tab in self._result_tabs().query("Tab")}
        result_tab_id = self._tab_id("result_tab", step_id)
        if result_tab_id not in result_tab_ids:
            self._result_tabs().add_tab(Tab(label, id=result_tab_id))

    def _reset_tabs(self) -> None:
        stream_tabs = self._stream_tabs()
        result_tabs = self._result_tabs()

        for tab in list(stream_tabs.query("Tab")):
            if tab.id != self._tab_id("stream_tab", self.GENERAL_STEP_ID):
                stream_tabs.remove_tab(tab.id)

        for tab in list(result_tabs.query("Tab")):
            if tab.id != self._tab_id("result_tab", self.GENERAL_STEP_ID):
                result_tabs.remove_tab(tab.id)

        stream_tabs.active = self._tab_id("stream_tab", self.GENERAL_STEP_ID)
        result_tabs.active = self._tab_id("result_tab", self.GENERAL_STEP_ID)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab.id is None:
            return

        if event.tabs.id == "stream_tabs":
            self._stream_selected_step_id = self._step_from_tab_id(event.tab.id, "stream_tab")
            self._refresh_stream_log()
        elif event.tabs.id == "result_tabs":
            self._result_selected_step_id = self._step_from_tab_id(event.tab.id, "result_tab")
            self._refresh_result_log()

    def _refresh_stream_log(self) -> None:
        stream_log = self._stream_log()
        stream_log.clear()
        for line in self._stream_buffers.get(self._stream_selected_step_id, []):
            stream_log.write(line)

    def _refresh_result_log(self) -> None:
        result_log = self._result_log()
        result_log.clear()
        for renderable in self._result_render_buffers.get(self._result_selected_step_id, []):
            result_log.write(renderable)

    def _append_stream_line(self, text: str, step_id: str | None = None) -> None:
        target_step = step_id or self._current_step_id
        self._stream_buffers.setdefault(self.GENERAL_STEP_ID, []).append(text)
        if target_step != self.GENERAL_STEP_ID:
            self._stream_buffers.setdefault(target_step, []).append(text)

        if self._stream_selected_step_id in {self.GENERAL_STEP_ID, target_step}:
            self._stream_log().write(text)

    def _append_result_renderable(
        self,
        renderable: object,
        plain_text: str,
        step_id: str | None = None,
    ) -> None:
        target_step = step_id or self._current_step_id
        self._result_render_buffers.setdefault(self.GENERAL_STEP_ID, []).append(renderable)
        self._result_text_buffers.setdefault(self.GENERAL_STEP_ID, []).append(plain_text)
        if target_step != self.GENERAL_STEP_ID:
            self._result_render_buffers.setdefault(target_step, []).append(renderable)
            self._result_text_buffers.setdefault(target_step, []).append(plain_text)

        if self._result_selected_step_id in {self.GENERAL_STEP_ID, target_step}:
            self._result_log().write(renderable)

    def set_status(self, text: str, style: str = "white") -> None:
        self._dispatch_ui(self._set_status, text, style)

    def _set_status(self, text: str, style: str) -> None:
        self.query_one("#status", Static).update(Text(text, style=style))

    def start_stream(self, title: str, style: str = "blue") -> None:
        self._dispatch_ui(self._start_stream, title, style)

    def _start_stream(self, title: str, style: str) -> None:
        del style
        stream_title = f"== {title} =="
        self._append_stream_line(stream_title)
        self.set_status(f"Executando: {title}", style="yellow")

    def append_stream(self, text: str) -> None:
        self._dispatch_ui(self._append_stream, text)

    def _append_stream(self, text: str) -> None:
        if text:
            self._append_stream_line(text)

    def finish_stream(self) -> None:
        self._dispatch_ui(self._finish_stream)

    def _finish_stream(self) -> None:
        separator = "----------------------------------------"
        self._append_stream_line(separator)

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
        del style
        if is_code:
            rendered_content: object = Syntax(content, language, theme="monokai", word_wrap=True)
        else:
            rendered_content = RichMarkdown(content)

        self._append_result_renderable(f"=== {title} ===", plain_text=f"=== {title} ===")
        self._append_result_renderable(rendered_content, plain_text=content)
        self._append_result_renderable("", plain_text="")

    def show_error(self, message: str) -> None:
        self._dispatch_ui(self._show_error, message)

    def _show_error(self, message: str) -> None:
        self._append_result_renderable("=== Erro ===", plain_text="=== Erro ===")
        self._append_result_renderable(message, plain_text=message)
        self._append_result_renderable("", plain_text="")
        self._set_status(message, style="red")

    def show_success(self, message: str) -> None:
        self._dispatch_ui(self._show_success, message)

    def _show_success(self, message: str) -> None:
        self._append_result_renderable("=== Sucesso ===", plain_text="=== Sucesso ===")
        self._append_result_renderable(message, plain_text=message)
        self._append_result_renderable("", plain_text="")
        self._set_status(message, style="green")

    def clear_logs(self) -> None:
        self._dispatch_ui(self._clear_logs)

    def _clear_logs(self) -> None:
        self._stream_log().clear()
        self._result_log().clear()
        self._stream_buffers = {self.GENERAL_STEP_ID: []}
        self._result_render_buffers = {self.GENERAL_STEP_ID: []}
        self._result_text_buffers = {self.GENERAL_STEP_ID: []}
        self._step_labels = {self.GENERAL_STEP_ID: "Geral"}
        self._stream_selected_step_id = self.GENERAL_STEP_ID
        self._result_selected_step_id = self.GENERAL_STEP_ID
        self._current_step_id = self.GENERAL_STEP_ID
        self._reset_tabs()
        self._set_status("Pronto.", style="white")

    def _set_running(self, running: bool) -> None:
        self._flow_running = running
        run_button = self.query_one("#run_button", Button)
        run_button.disabled = running
        run_button.label = self.RUN_LABEL_RUNNING if running else self.RUN_LABEL_IDLE
        self.query_one("#prompt_input", Input).disabled = running
        self.query_one("#flow_input", Input).disabled = running
        self.query_one("#clear_button", Button).disabled = running and not self._awaiting_feedback
        self.query_one("#abort_button", Button).disabled = not running and not self._awaiting_feedback

    def _normalize_path_key(self, path: Path) -> str:
        try:
            return str(path.resolve())
        except OSError:
            return str(path)

    def _confirm_implicit_flow_if_needed(
        self,
        resolved_flow_config: ResolvedFlowConfig,
        flow_path: str | None,
    ) -> bool:
        if flow_path is not None:
            self._pending_auto_flow_confirmation = None
            return True

        if (
            resolved_flow_config.source not in {FLOW_CONFIG_SOURCE_CWD, FLOW_CONFIG_SOURCE_ENV}
            or resolved_flow_config.path is None
        ):
            self._pending_auto_flow_confirmation = None
            return True

        path_key = self._normalize_path_key(resolved_flow_config.path)
        if path_key in self._trusted_auto_flow_paths:
            self._pending_auto_flow_confirmation = None
            return True

        if self._pending_auto_flow_confirmation != path_key:
            self._pending_auto_flow_confirmation = path_key
            source_label = (
                "COUNCIL_FLOW_CONFIG"
                if resolved_flow_config.source == FLOW_CONFIG_SOURCE_ENV
                else "./flow.json"
            )
            self._set_status(
                f"Detectada configuração implícita via {source_label}. "
                "Pressione Executar novamente para confirmar ou informe um caminho explícito "
                "no campo de fluxo.",
                style="yellow",
            )
            return False

        self._trusted_auto_flow_paths.add(path_key)
        self._pending_auto_flow_confirmation = None
        return True

    def _start_execution(self) -> None:
        if self._flow_running:
            return

        prompt = self.query_one("#prompt_input", Input).value.strip()
        if not prompt:
            self._set_status("Informe um prompt antes de executar.", style="red")
            return

        flow_path = self.query_one("#flow_input", Input).value.strip() or None
        try:
            resolved_flow_config = resolve_flow_config(flow_path)
        except ConfigError as exc:
            self._set_status(f"Erro ao carregar configuração do fluxo: {exc}", style="red")
            return

        if not self._confirm_implicit_flow_if_needed(resolved_flow_config, flow_path):
            return

        self._last_prompt_value = prompt
        self._last_flow_config_value = flow_path or ""
        self._remember_prompt(prompt)
        self._persist_state(last_prompt=prompt, last_flow_config=flow_path or "")
        self.clear_logs()
        self._set_running(True)
        self._set_status("Preparando execução...", style="yellow")
        thread = threading.Thread(
            target=self.run_council_flow,
            args=(prompt, flow_path, resolved_flow_config),
            daemon=True,
        )
        thread.start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run_button":
            self._start_execution()
        elif event.button.id == "clear_button":
            self.clear_logs()
        elif event.button.id == "copy_stream_button":
            self.action_copy_stream()
        elif event.button.id == "copy_results_button":
            self.action_copy_results()
        elif event.button.id == "continue_button" and self._awaiting_feedback:
            self._resolve_feedback(None)
        elif event.button.id == "send_feedback_button" and self._awaiting_feedback:
            self._submit_feedback_from_input()
        elif event.button.id == "abort_button":
            self.action_abort_flow()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"prompt_input", "flow_input"} and not self._awaiting_feedback:
            self._start_execution()
        elif event.input.id == "feedback_input" and self._awaiting_feedback:
            self._submit_feedback_from_input()

    def action_run_flow(self) -> None:
        self._start_execution()

    def action_clear_logs(self) -> None:
        self.clear_logs()

    def action_abort_flow(self) -> None:
        if not self._flow_running and not self._awaiting_feedback:
            self.set_status("Nenhuma execução ativa para abortar.", style="yellow")
            return

        self.set_status("Abortando execução...", style="yellow")

        with self._executor_lock:
            executor = self._active_executor
        if executor is not None:
            executor.request_cancel()

        if self._awaiting_feedback:
            self._resolve_feedback("__ABORT__")

    def action_copy_stream(self) -> None:
        selected_step = self._stream_selected_step_id
        selected_label = self._step_labels.get(selected_step, selected_step)
        self._copy_text_payload(
            payload="\n".join(self._stream_buffers.get(selected_step, [])),
            label=f"stream_{selected_label}",
            empty_message="Stream vazio para copiar.",
        )

    def action_copy_results(self) -> None:
        selected_step = self._result_selected_step_id
        selected_label = self._step_labels.get(selected_step, selected_step)
        self._copy_text_payload(
            payload="\n".join(self._result_text_buffers.get(selected_step, [])),
            label=f"resultados_{selected_label}",
            empty_message="Resultados vazios para copiar.",
        )

    def action_quit_app(self) -> None:
        if self._flow_running or self._awaiting_feedback:
            self.action_abort_flow()
        self.exit()

    def _copy_text_payload(self, payload: str, label: str, empty_message: str) -> None:
        if not payload.strip():
            self.set_status(empty_message, style="yellow")
            return

        safe_label = self._sanitize_filename_segment(label)
        try:
            self.copy_to_clipboard(payload)
            self.set_status(f"{label} copiado para clipboard.", style="green")
        except Exception:
            try:
                path, directory_secured = self._save_clipboard_fallback(
                    payload=payload,
                    safe_label=safe_label,
                )
            except OSError:
                self.set_status(
                    "Clipboard indisponível. Falha ao salvar fallback seguro em COUNCIL_HOME.",
                    style="red",
                )
                return
            warning_suffix = ""
            if not directory_secured:
                warning_suffix = " (aviso: permissões do diretório não puderam ser restritas)"
            self.set_status(
                f"Clipboard indisponível. Conteúdo salvo em {path}{warning_suffix}",
                style="yellow",
            )

    def _save_clipboard_fallback(self, payload: str, safe_label: str) -> tuple[Path, bool]:
        fallback_dir = self._get_clipboard_fallback_dir()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        directory_secured = self._secure_directory_permissions(fallback_dir)
        self._cleanup_clipboard_fallback_files(fallback_dir)

        fd, raw_path = tempfile.mkstemp(
            prefix=f"{self.CLIPBOARD_FALLBACK_FILE_PREFIX}{safe_label}_",
            suffix=".txt",
            dir=str(fallback_dir),
            text=True,
        )
        saved_path = Path(raw_path)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                saved_path.unlink()
            except OSError:
                pass
            raise

        os.chmod(saved_path, 0o600)
        return saved_path, directory_secured

    def _get_clipboard_fallback_dir(self) -> Path:
        return get_council_home(create=True) / self.CLIPBOARD_FALLBACK_DIR_NAME

    def _cleanup_clipboard_fallback_files(self, directory: Path, now: float | None = None) -> None:
        reference_time = now if now is not None else time.time()
        cutoff = reference_time - self.CLIPBOARD_FALLBACK_RETENTION_SECONDS
        pattern = f"{self.CLIPBOARD_FALLBACK_FILE_PREFIX}*.txt"

        for candidate in directory.glob(pattern):
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except OSError:
                continue

    def _secure_directory_permissions(self, directory: Path) -> bool:
        try:
            os.chmod(directory, 0o700)
            return True
        except OSError:
            return False

    def _sanitize_filename_segment(self, value: str) -> str:
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        sanitized = "".join(char if char in allowed_chars else "_" for char in value)
        return sanitized.strip("_") or "copia"

    def _remember_prompt(self, prompt: str) -> None:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            return

        self._prompt_history = [item for item in self._prompt_history if item != cleaned_prompt]
        self._prompt_history.append(cleaned_prompt)

        if len(self._prompt_history) > self.MAX_HISTORY_ITEMS:
            self._prompt_history = self._prompt_history[-self.MAX_HISTORY_ITEMS :]

        self._history_index = len(self._prompt_history)
        self._history_draft = ""

    def _navigate_prompt_history_up(self) -> None:
        if not self._prompt_history:
            self.set_status("Histórico de prompts vazio.", style="yellow")
            return

        prompt_input = self.query_one("#prompt_input", Input)
        if self._history_index == len(self._prompt_history):
            self._history_draft = prompt_input.value

        if self._history_index > 0:
            self._history_index -= 1

        prompt_input.value = self._prompt_history[self._history_index]
        prompt_input.cursor_position = len(prompt_input.value)

    def _navigate_prompt_history_down(self) -> None:
        if not self._prompt_history:
            self.set_status("Histórico de prompts vazio.", style="yellow")
            return

        prompt_input = self.query_one("#prompt_input", Input)
        history_size = len(self._prompt_history)

        if self._history_index < history_size - 1:
            self._history_index += 1
            prompt_input.value = self._prompt_history[self._history_index]
        elif self._history_index == history_size - 1:
            self._history_index = history_size
            prompt_input.value = self._history_draft

        prompt_input.cursor_position = len(prompt_input.value)

    def _persist_state(self, last_prompt: str | None = None, last_flow_config: str | None = None) -> None:
        if last_prompt is not None:
            self._last_prompt_value = last_prompt
        if last_flow_config is not None:
            self._last_flow_config_value = last_flow_config

        live_prompt_value = self._safe_input_value(input_id="prompt_input", fallback=self._last_prompt_value)
        live_flow_value = self._safe_input_value(input_id="flow_input", fallback=self._last_flow_config_value)

        self._last_prompt_value = live_prompt_value
        self._last_flow_config_value = live_flow_value

        state_payload = {
            "last_prompt": self._last_prompt_value,
            "last_flow_config": self._last_flow_config_value,
            "prompt_history": self._prompt_history,
        }

        try:
            passphrase = read_tui_state_passphrase() or None
            persist_tui_state_payload(
                path=self.STATE_FILE_PATH,
                payload=state_payload,
                passphrase=passphrase,
            )
        except TUIStateCryptoUnavailableError:
            # Fail-closed: não persiste prompts em texto plano quando a criptografia foi solicitada.
            sanitized_payload = dict(state_payload)
            sanitized_payload["last_prompt"] = ""
            sanitized_payload["prompt_history"] = []
            try:
                persist_tui_state_payload(
                    path=self.STATE_FILE_PATH,
                    payload=sanitized_payload,
                    passphrase=None,
                )
            except OSError:
                pass
            self._notify_state_warning(
                "Criptografia de estado solicitada, mas 'cryptography' não está instalado. "
                "Prompts sensíveis não foram persistidos."
            )
        except TUIStateCryptoError as exc:
            self._notify_state_warning(f"Falha ao proteger estado da TUI: {exc}")
        except OSError:
            pass

    def _notify_state_warning(self, message: str) -> None:
        self._state_warning = message
        try:
            self._set_status(message, style="yellow")
        except Exception:
            pass

    def _safe_input_value(self, input_id: str, fallback: str) -> str:
        try:
            return self.query_one(f"#{input_id}", Input).value.strip()
        except Exception:
            return fallback

    def _load_persisted_state(self) -> dict[str, object]:
        return self._read_state_payload(self.STATE_FILE_PATH)

    def _read_state_payload(self, path: Path) -> dict[str, object]:
        try:
            return load_tui_state_payload(path=path, passphrase=read_tui_state_passphrase() or None)
        except TUIStateCryptoError as exc:
            self._state_warning = str(exc)
            fallback_payload = read_raw_tui_state_payload(path)
            fallback_payload.pop("last_prompt", None)
            fallback_payload.pop("prompt_history", None)
            fallback_payload.pop("encrypted_prompt_state", None)
            return fallback_payload
        except OSError:
            return {}

    def _normalize_prompt_history(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []

        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned or cleaned in normalized:
                continue
            normalized.append(cleaned)

        if len(normalized) > self.MAX_HISTORY_ITEMS:
            normalized = normalized[-self.MAX_HISTORY_ITEMS :]
        return normalized

    def _coerce_string(self, value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    def request_step_feedback(self, agent_name: str, role_desc: str, output: str) -> str | None:
        del output
        self._feedback_event.clear()
        self._feedback_value = None
        self._dispatch_ui(self._begin_feedback_mode, agent_name, role_desc)
        self._feedback_event.wait()
        selected_feedback = self._feedback_value
        self._dispatch_ui(self._end_feedback_mode)

        if selected_feedback == "__ABORT__":
            raise ExecutionAborted("Execução abortada pelo usuário.")
        return selected_feedback

    def _begin_feedback_mode(self, agent_name: str, role_desc: str) -> None:
        self._awaiting_feedback = True
        self.query_one("#feedback_hint", Static).update(
            f"Aguardando você: {agent_name} ({role_desc}). "
            "Use 'Continuar' para próximo agente ou envie ajuste."
        )
        self.query_one("#feedback_input", Input).disabled = False
        self.query_one("#feedback_input", Input).value = ""
        self.query_one("#continue_button", Button).disabled = False
        self.query_one("#send_feedback_button", Button).disabled = False
        self.query_one("#abort_button", Button).disabled = False
        self.query_one("#clear_button", Button).disabled = True
        self.set_status("Checkpoint humano ativo.", style="yellow")
        self.query_one("#feedback_input", Input).focus()

    def _end_feedback_mode(self) -> None:
        self._awaiting_feedback = False
        self.query_one("#feedback_hint", Static).update(
            "Checkpoint por etapa: aguarde a saída do agente para Continuar ou Enviar ajuste."
        )
        self.query_one("#feedback_input", Input).disabled = True
        self.query_one("#feedback_input", Input).value = ""
        self.query_one("#continue_button", Button).disabled = True
        self.query_one("#send_feedback_button", Button).disabled = True
        self.query_one("#abort_button", Button).disabled = not self._flow_running
        self.query_one("#clear_button", Button).disabled = self._flow_running
        self.set_status("Executando próximo passo...", style="yellow")

    def _submit_feedback_from_input(self) -> None:
        feedback = self.query_one("#feedback_input", Input).value.strip()
        if not feedback:
            self.set_status("Digite um ajuste ou use 'Continuar'.", style="yellow")
            return
        self._resolve_feedback(feedback)

    def _resolve_feedback(self, value: str | None) -> None:
        self._feedback_value = value
        self._feedback_event.set()

    def run_council_flow(
        self,
        prompt: str,
        flow_config: str | None,
        resolved_flow_config: ResolvedFlowConfig | None = None,
    ) -> None:
        ui = TextualUIAdapter(self)
        log_event(
            self._audit_logger,
            "tui.run.invoked",
            level=logging.INFO,
            flow_config_arg=flow_config or "",
            prompt_chars=len(prompt),
            flow_config_source=(
                resolved_flow_config.source if resolved_flow_config is not None else "default"
            ),
        )
        try:
            state = CouncilState()
            executor = Executor(ui)
        except ValueError as exc:
            log_event(
                self._audit_logger,
                "tui.run.invalid_limits",
                level=logging.ERROR,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            ui.show_error(f"Configuração inválida de limites: {exc}")
            self._dispatch_ui(self._set_running, False)
            return
        with self._executor_lock:
            self._active_executor = executor

        try:
            flow_steps = load_flow_steps(flow_config, resolved_config=resolved_flow_config)
        except ConfigError as exc:
            log_event(
                self._audit_logger,
                "tui.run.invalid_flow_config",
                level=logging.ERROR,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            ui.show_error(f"Erro ao carregar configuração do fluxo: {exc}")
            self._dispatch_ui(self._set_running, False)
            return

        prerequisite_statuses = evaluate_flow_prerequisites(flow_steps)
        missing_binaries = find_missing_binaries(prerequisite_statuses)
        if missing_binaries:
            missing_bins_text = ", ".join(sorted(status.binary for status in missing_binaries))
            log_event(
                self._audit_logger,
                "tui.run.prerequisites_missing",
                level=logging.ERROR,
                missing_binaries=sorted(status.binary for status in missing_binaries),
                planned_steps=len(flow_steps),
            )
            ui.show_error(
                (
                    "Pré-requisitos ausentes no PATH para executar o fluxo: "
                    f"{missing_bins_text}. Execute 'council doctor' para diagnóstico."
                )
            )
            self._dispatch_ui(self._set_running, False)
            return

        risky_binary_locations = find_world_writable_binary_locations(prerequisite_statuses)
        if risky_binary_locations:
            details = ", ".join(
                f"{status.binary} ({status.resolved_path or 'caminho desconhecido'})"
                for status in risky_binary_locations
            )
            self._dispatch_ui(
                self._set_status,
                (
                    "Aviso de segurança: binários resolvidos em diretório gravável por outros "
                    f"usuários: {details}"
                ),
                "yellow",
            )

        history_store: HistoryStore | None = None
        try:
            history_store = HistoryStore()
        except OSError as exc:
            log_event(
                self._audit_logger,
                "tui.run.history_store_unavailable",
                level=logging.ERROR,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            ui.show_error(f"Aviso: persistência estruturada indisponível: {exc}")

        orchestrator = Orchestrator(
            state,
            executor,
            ui,
            flow_steps=flow_steps,
            history_store=history_store,
            flow_config_path=(
                str(resolved_flow_config.path)
                if resolved_flow_config is not None and resolved_flow_config.path is not None
                else None
            ),
            flow_config_source=(resolved_flow_config.source if resolved_flow_config is not None else None),
        )

        try:
            orchestrator.run_flow(prompt)
        except Exception as exc:
            log_event(
                self._audit_logger,
                "tui.run.unexpected_error",
                level=logging.ERROR,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            ui.show_error(f"Erro inesperado na execução: {exc}")
        finally:
            log_event(
                self._audit_logger,
                "tui.run.finished",
                level=logging.INFO,
                flow_config_source=(
                    resolved_flow_config.source if resolved_flow_config is not None else "default"
                ),
            )
            self._feedback_event.set()
            with self._executor_lock:
                self._active_executor = None
            self._dispatch_ui(self._set_running, False)


def run_tui(initial_prompt: str = "", initial_flow_config: str = "") -> None:
    CouncilTextualApp(
        initial_prompt=initial_prompt,
        initial_flow_config=initial_flow_config,
    ).run()
