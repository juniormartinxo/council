import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.events import Mount
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from council.config import (
    ALLOWED_COMMAND_BINARIES,
    FlowStep,
    get_default_flow_steps,
    load_flow_steps,
)
from council.flow_signature import get_signature_file_path

DEFAULT_INPUT_TEMPLATE = "{instruction}\n\n{full_context}"
DEFAULT_STEP_PROFILE_OPTIONS = (
    ("plan", "Planejamento"),
    ("critique", "Crítica"),
    ("final_plan", "Consolidação"),
    ("implement", "Implementação"),
    ("code", "Implementação"),
    ("review", "Revisão"),
    ("final_review", "Revisão Final"),
)
DEFAULT_STYLE_OPTIONS = (
    "blue",
    "dark_goldenrod",
    "dodger_blue1",
    "bright_black",
    "green",
    "yellow",
    "magenta",
    "cyan",
    "red",
    "white",
)
STEP_PROFILE_SEPARATOR = "||"
DEFAULT_AGENT_OPTIONS = (
    "Claude",
    "Gemini",
    "Codex",
    "Ollama",
)
DEFAULT_COMMAND_BY_AGENT = {
    "claude": "claude -p",
    "gemini": "gemini -p {input}",
    "codex": "codex exec --skip-git-repo-check",
    "ollama": "ollama run llama3.1",
}


class StepListItem(ListItem):
    """Item interativo da lista que representa um passo do fluxo."""

    def __init__(self, step: FlowStep, step_index: int) -> None:
        super().__init__()
        self.step = step
        self.step_index = step_index
        self._label = Label(self._format_label())

    def _format_label(self) -> str:
        return f"{self.step_index + 1}. {self.step.agent_name} ({self.step.key})"

    def compose(self) -> ComposeResult:
        yield self._label

    def update_label(self) -> None:
        self._label.update(self._format_label())


class SaveAsScreen(ModalScreen[str]):
    """Tela modal para solicitar o caminho de salvamento do fluxo."""

    CSS = """
    SaveAsScreen {
        align: center middle;
    }
    #dialog {
        padding: 1 2;
        width: 60;
        height: 15;
        border: thick $primary 80%;
        background: $surface;
    }
    .dialog-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dialog-buttons {
        margin-top: 1;
        layout: horizontal;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, default_path: str = "flow.json") -> None:
        super().__init__()
        self.default_path = default_path

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Salvar Fluxo Como:", classes="dialog-title")
            yield Input(id="in-path", value=self.default_path)
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancelar", variant="default", id="btn-cancel")
                yield Button("Salvar", variant="success", id="btn-save")

    def on_mount(self) -> None:
        self.query_one("#in-path", Input).focus()

    @on(Button.Pressed, "#btn-save")
    def _save(self) -> None:
        path = self.query_one("#in-path", Input).value.strip()
        if path:
            self.dismiss(path)

    @on(Button.Pressed, "#btn-cancel")
    def _cancel(self) -> None:
        self.dismiss("")

    @on(Input.Submitted, "#in-path")
    def _submit(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        if path:
            self.dismiss(path)


class FlowConfigApp(App[None]):
    """Editor TUI para o arquivo flow.json do Council."""

    CSS = """
    Screen {
        layers: base overlay;
    }

    #main-container {
        layout: horizontal;
        height: 100%;
    }

    #sidebar {
        width: 34;
        height: 100%;
        border-right: solid $primary;
        background: $boost;
    }

    #sidebar-header {
        height: 3;
        content-align: center middle;
        text-style: bold;
        background: $panel;
    }

    #step-list {
        height: 1fr;
    }

    #sidebar-actions {
        height: auto;
        padding: 1;
        layout: vertical;
    }

    #sidebar-actions > Button {
        width: 100%;
        margin-bottom: 1;
    }

    #order-actions {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
    }

    #order-actions Button {
        width: 1fr;
    }

    #btn-up {
        margin-right: 1;
    }

    #form-container {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    .form-group {
        height: auto;
        margin-bottom: 1;
    }

    .form-group > Label {
        text-style: bold;
        margin-bottom: 1;
        color: $text-muted;
    }

    .form-group > Input {
        width: 100%;
    }

    .form-group > Select {
        width: 100%;
    }

    .form-group > TextArea {
        height: 6;
        width: 100%;
    }
    
    .warning {
        color: $warning;
        text-style: italic;
        margin-top: 1;
    }

    #empty-state {
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }

    #step-form {
        width: 100%;
        height: auto;
    }

    #form-grid {
        layout: horizontal;
        width: 100%;
        height: auto;
    }

    #form-left {
        width: 1fr;
        margin-right: 2;
    }

    #form-right {
        width: 1fr;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        ("ctrl+s", "save_flow", "Salvar"),
        ("ctrl+q", "quit_app", "Sair"),
        ("ctrl+n", "new_step", "Novo Passo"),
    ]

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path
        self.steps: list[FlowStep] = []
        self.current_step_index: int | None = None
        self.is_new_file = False
        self._is_populating_form = False

    def on_mount(self) -> None:
        self.title = "Council Flow Editor"
        self._load_initial_data()

    def _load_initial_data(self) -> None:
        if self.config_path and self.config_path.exists():
            self.sub_title = str(self.config_path)
            try:
                # Usa func do config.py mas intercepta erros simples
                self.steps = load_flow_steps(str(self.config_path))
            except Exception as e:
                self.notify(f"Erro ao carregar flow: {e}", severity="error", timeout=5)
                self.steps = get_default_flow_steps()
                self.is_new_file = True
        else:
            self.sub_title = "Novo Arquivo (Não Salvo)"
            self.steps = get_default_flow_steps()
            self.is_new_file = True

        self._refresh_list()
        
        # Seleciona o primeiro passo automaticamente se existir
        list_view = self.query_one("#step-list", ListView)
        if self.steps and len(list_view.children) > 0:
            list_view.index = 0
            self.current_step_index = 0
            self._populate_form(self.steps[0])

    def compose(self) -> ComposeResult:
        yield Header()
        
        with Horizontal(id="main-container"):
            # Sidebar
            with Vertical(id="sidebar"):
                yield Static("Passos do Fluxo", id="sidebar-header")
                yield ListView(id="step-list")
                with Vertical(id="sidebar-actions"):
                    yield Button("Novo Passo (Ctrl+N)", id="btn-new", variant="success")
                    # Controles de ordem aparecem apenas quando há seleção
                    with Horizontal(id="order-actions"):
                        yield Button("Subir Passo", id="btn-up", variant="primary")
                        yield Button("Descer Passo", id="btn-down", variant="primary")
                    yield Button("Remover", id="btn-delete", variant="error")

            # Área principal do formulário
            with ScrollableContainer(id="form-container"):
                yield Static("Selecione um passo na lateral para editar.", id="empty-state")
                
                # Container do formulário em si (oculto inicialmente)
                with Vertical(id="step-form", classes="hidden"):
                    with Horizontal(id="form-grid"):
                        with Vertical(id="form-left"):
                            with Vertical(classes="form-group"):
                                yield Label("Perfil do passo (key + role_desc)")
                                yield Select(
                                    self._profile_options_from_pairs(DEFAULT_STEP_PROFILE_OPTIONS),
                                    id="sel-step-profile",
                                    allow_blank=False,
                                )

                            with Vertical(classes="form-group"):
                                yield Label("Comando CLI (command)")
                                yield Input(id="in-command", placeholder="ex: gemini -p {input}")
                                yield Label("", id="lbl-cmd-warning", classes="warning hidden")

                            with Vertical(classes="form-group"):
                                yield Label("Instrução Base (instruction)")
                                yield TextArea(id="ta-instruction", language="markdown")

                            with Vertical(classes="form-group"):
                                yield Label("Opções")
                                yield Checkbox("É resultado de código? (is_code)", id="cb-is-code")

                            with Vertical(classes="form-group"):
                                yield Label("Max Input Chars")
                                yield Input(id="in-max-input", placeholder="Vazio = Padrão")

                        with Vertical(id="form-right"):
                            with Vertical(classes="form-group"):
                                yield Label("Nome Visível (agent_name)")
                                yield Select(
                                    self._options_from_values(DEFAULT_AGENT_OPTIONS),
                                    id="sel-agent-name",
                                    allow_blank=False,
                                )

                            with Vertical(classes="form-group"):
                                yield Label("Estilo visual (style)")
                                yield Select(
                                    self._options_from_values(DEFAULT_STYLE_OPTIONS),
                                    id="sel-style",
                                    allow_blank=False,
                                )

                            with Vertical(classes="form-group"):
                                yield Label("Timeout (segundos)")
                                yield Input(id="in-timeout", placeholder="120")

                            with Vertical(classes="form-group"):
                                yield Label("Template de Input (input_template)")
                                yield TextArea(id="ta-input-template")

                            with Vertical(classes="form-group"):
                                yield Label("Max Output Chars")
                                yield Input(id="in-max-output", placeholder="Vazio = Padrão")
                            
                            with Vertical(classes="form-group"):
                                yield Label("Max Context Chars (Histórico)")
                                yield Input(id="in-max-context", placeholder="Vazio = Padrão")

        yield Footer()

    def _refresh_list(self) -> None:
        list_view = self.query_one("#step-list", ListView)
        current_index = list_view.index
        list_view.clear()
        
        for i, step in enumerate(self.steps):
            list_view.append(StepListItem(step, i))
            
        if current_index is not None and self.steps:
            # Mantém a seleção na posição anterior, limitando ao novo tamanho
            new_index = min(current_index, len(self.steps) - 1)
            list_view.index = new_index

    @staticmethod
    def _options_from_values(values: tuple[str, ...] | list[str]) -> list[tuple[str, str]]:
        return [(value, value) for value in values]

    @staticmethod
    def _encode_step_profile_value(key: str, role_desc: str) -> str:
        return f"{key}{STEP_PROFILE_SEPARATOR}{role_desc}"

    @staticmethod
    def _decode_step_profile_value(profile_value: str) -> tuple[str, str] | None:
        if STEP_PROFILE_SEPARATOR not in profile_value:
            return None
        key, role_desc = profile_value.split(STEP_PROFILE_SEPARATOR, 1)
        cleaned_key = key.strip()
        cleaned_role = role_desc.strip()
        if not cleaned_key or not cleaned_role:
            return None
        return cleaned_key, cleaned_role

    @classmethod
    def _profile_options_from_pairs(
        cls,
        pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []
        seen: set[str] = set()
        for key, role_desc in pairs:
            encoded_value = cls._encode_step_profile_value(key, role_desc)
            if encoded_value in seen:
                continue
            seen.add(encoded_value)
            label = f"{key} ({role_desc})"
            options.append((label, encoded_value))
        return options

    @staticmethod
    def _merge_options(base_values: tuple[str, ...], dynamic_values: list[str], current: str) -> list[str]:
        merged = list(base_values)
        for value in dynamic_values:
            if value and value not in merged:
                merged.append(value)
        if current and current not in merged:
            merged.append(current)
        return merged

    @staticmethod
    def _merge_profile_pairs(
        base_pairs: tuple[tuple[str, str], ...],
        dynamic_pairs: list[tuple[str, str]],
        current_pair: tuple[str, str],
    ) -> list[tuple[str, str]]:
        merged = list(base_pairs)
        for pair in dynamic_pairs:
            if pair not in merged and pair[0] and pair[1]:
                merged.append(pair)
        if current_pair not in merged and current_pair[0] and current_pair[1]:
            merged.append(current_pair)
        return merged

    def _read_select_value(self, selector: str, fallback: str) -> str:
        select = self.query_one(selector, Select)
        value = select.value
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return fallback

    @staticmethod
    def _resolve_default_command_for_agent(agent_name: str) -> str | None:
        return DEFAULT_COMMAND_BY_AGENT.get(agent_name.strip().lower())

    @on(ListView.Selected, "#step-list")
    def _on_step_selected(self, event: ListView.Selected) -> None:
        item = cast(StepListItem, event.item)
        self.current_step_index = item.step_index
        self._populate_form(item.step)
        
    @on(ListView.Highlighted, "#step-list")
    def _on_step_highlighted(self, event: ListView.Highlighted) -> None:
         # Salva automaticamente o formulário atual antes de trocar de tab (se houver algo válido selecionado)
         if self.current_step_index is not None:
             self._save_form_to_step(self.current_step_index)
             
         if event.item is not None:
             item = cast(StepListItem, event.item)
             self.current_step_index = item.step_index
             self._populate_form(item.step)

    def _populate_form(self, step: FlowStep) -> None:
        self.query_one("#empty-state").add_class("hidden")
        form = self.query_one("#step-form")
        form.remove_class("hidden")
        self._is_populating_form = True

        try:
            profile_options = self._merge_profile_pairs(
                DEFAULT_STEP_PROFILE_OPTIONS,
                [(existing_step.key, existing_step.role_desc) for existing_step in self.steps],
                (step.key, step.role_desc),
            )
            profile_select = self.query_one("#sel-step-profile", Select)
            profile_select.set_options(self._profile_options_from_pairs(profile_options))
            profile_select.value = self._encode_step_profile_value(step.key, step.role_desc)

            agent_options = self._merge_options(
                DEFAULT_AGENT_OPTIONS,
                [existing_step.agent_name for existing_step in self.steps],
                step.agent_name,
            )
            agent_select = self.query_one("#sel-agent-name", Select)
            agent_select.set_options(self._options_from_values(agent_options))
            agent_select.value = step.agent_name

            style_options = self._merge_options(DEFAULT_STYLE_OPTIONS, [], step.style)
            style_select = self.query_one("#sel-style", Select)
            style_select.set_options(self._options_from_values(style_options))
            style_select.value = step.style
            
            cmd_input = self.query_one("#in-command", Input)
            cmd_input.value = step.command
            self._validate_command_live(step.command)

            txt_instruction = self.query_one("#ta-instruction", TextArea)
            txt_instruction.text = step.instruction

            txt_template = self.query_one("#ta-input-template", TextArea)
            txt_template.text = step.input_template

            self.query_one("#cb-is-code", Checkbox).value = step.is_code
            self.query_one("#in-timeout", Input).value = str(step.timeout)
            self.query_one("#in-max-input", Input).value = str(step.max_input_chars) if step.max_input_chars else ""
            self.query_one("#in-max-output", Input).value = str(step.max_output_chars) if step.max_output_chars else ""
            self.query_one("#in-max-context", Input).value = str(step.max_context_chars) if step.max_context_chars else ""
        finally:
            self._is_populating_form = False

    @on(Select.Changed, "#sel-agent-name")
    def _on_agent_changed(self, event: Select.Changed) -> None:
        if self._is_populating_form:
            return
        if not isinstance(event.value, str):
            return
        command = self._resolve_default_command_for_agent(event.value)
        if command is None:
            return
        cmd_input = self.query_one("#in-command", Input)
        cmd_input.value = command
        self._validate_command_live(command)

    @on(Input.Changed, "#in-command")
    def _on_command_changed(self, event: Input.Changed) -> None:
        self._validate_command_live(event.value)

    def _validate_command_live(self, command: str) -> None:
        warning_lbl = self.query_one("#lbl-cmd-warning", Label)
        if not command.strip():
            warning_lbl.update("Comando vazio.")
            warning_lbl.remove_class("hidden")
            return
            
        binary = command.split()[0]
        if binary not in ALLOWED_COMMAND_BINARIES:
            warning_lbl.update(f"⚠️  Binário '{binary}' não está na allowlist global.")
            warning_lbl.remove_class("hidden")
        elif shutil.which(binary) is None:
            warning_lbl.update(f"ℹ️  Binário '{binary}' não encontrado no PATH atual. (Aviso)")
            warning_lbl.remove_class("hidden")
        else:
            warning_lbl.add_class("hidden")
            
    def _parse_int_field(self, value: str) -> int | None:
        val = value.strip()
        if not val:
            return None
        try:
            parsed = int(val)
            return parsed if parsed > 0 else None
        except ValueError:
            return None

    def _save_form_to_step(self, index: int) -> None:
        if index < 0 or index >= len(self.steps):
            return

        default_profile_value = self._encode_step_profile_value(f"step_{index + 1}", "Role")
        profile_value = self._read_select_value("#sel-step-profile", default_profile_value)
        decoded_profile = self._decode_step_profile_value(profile_value)
        if decoded_profile is None:
            key = f"step_{index + 1}"
            role_desc = "Role"
        else:
            key, role_desc = decoded_profile
        agent_name = self._read_select_value("#sel-agent-name", "Agent")
        style = self._read_select_value("#sel-style", "blue")
        command = self.query_one("#in-command", Input).value.strip() or "echo 'no command'"
        instruction = self.query_one("#ta-instruction", TextArea).text.strip()
        input_template = (
            self.query_one("#ta-input-template", TextArea).text.strip() or DEFAULT_INPUT_TEMPLATE
        )
        is_code = self.query_one("#cb-is-code", Checkbox).value

        timeout_val = self._parse_int_field(self.query_one("#in-timeout", Input).value)
        timeout = timeout_val if timeout_val is not None else 120
        max_input_chars = self._parse_int_field(self.query_one("#in-max-input", Input).value)
        max_output_chars = self._parse_int_field(self.query_one("#in-max-output", Input).value)
        max_context_chars = self._parse_int_field(self.query_one("#in-max-context", Input).value)

        updated_step = FlowStep(
            key=key,
            agent_name=agent_name,
            role_desc=role_desc,
            command=command,
            instruction=instruction,
            input_template=input_template,
            style=style,
            is_code=is_code,
            timeout=timeout,
            max_input_chars=max_input_chars,
            max_output_chars=max_output_chars,
            max_context_chars=max_context_chars,
        )
        self.steps[index] = updated_step

        # Opcional: Atualiza label na lista
        list_view = self.query_one("#step-list", ListView)
        if index < len(list_view.children):
            item = cast(StepListItem, list_view.children[index])
            item.step = updated_step
            item.update_label()

    @on(Button.Pressed, "#btn-new")
    def action_new_step(self) -> None:
        # Salva o atual se houver
        if self.current_step_index is not None:
             self._save_form_to_step(self.current_step_index)
             
        new_step = FlowStep(
            key=f"step_{len(self.steps) + 1}",
            agent_name="Novo Agente",
            role_desc="Descrição do Papel",
            command="",
            instruction="",
            input_template=DEFAULT_INPUT_TEMPLATE
        )
        self.steps.append(new_step)
        self._refresh_list()
        
        list_view = self.query_one("#step-list", ListView)
        list_view.index = len(self.steps) - 1

    @on(Button.Pressed, "#btn-delete")
    def _delete_step(self) -> None:
        if self.current_step_index is None or not self.steps:
            return
            
        idx = self.current_step_index
        self.steps.pop(idx)
        self.current_step_index = None
        
        self.query_one("#step-form").add_class("hidden")
        self.query_one("#empty-state").remove_class("hidden")
        
        self._refresh_list()
        
        # Tenta selecionar o próximo ou anterior
        list_view = self.query_one("#step-list", ListView)
        if self.steps:
            list_view.index = min(idx, len(self.steps) - 1)

    @on(Button.Pressed, "#btn-up")
    def _move_up(self) -> None:
        if self.current_step_index is None or self.current_step_index == 0:
            return
            
        self._save_form_to_step(self.current_step_index)
        idx = self.current_step_index
        
        # Realiza o swap
        self.steps[idx - 1], self.steps[idx] = self.steps[idx], self.steps[idx - 1]
        self._refresh_list()
        
        list_view = self.query_one("#step-list", ListView)
        list_view.index = idx - 1

    @on(Button.Pressed, "#btn-down")
    def _move_down(self) -> None:
         if self.current_step_index is None or self.current_step_index >= len(self.steps) - 1:
            return
            
         self._save_form_to_step(self.current_step_index)
         idx = self.current_step_index
         
         # Realiza o swap
         self.steps[idx + 1], self.steps[idx] = self.steps[idx], self.steps[idx + 1]
         self._refresh_list()
         
         list_view = self.query_one("#step-list", ListView)
         list_view.index = idx + 1

    def action_quit_app(self) -> None:
        self.exit()

    def action_save_flow(self) -> None:
        if self.current_step_index is not None:
             self._save_form_to_step(self.current_step_index)
             
        if not self.config_path:
            def check_reply(path_str: str | None) -> None:
                if path_str:
                    self.config_path = Path(path_str).expanduser()
                    self._execute_save()
            self.push_screen(SaveAsScreen(), check_reply)
            return

        self._execute_save()

    def _execute_save(self) -> None:
        if not self.config_path:
            return

        try:
             # Serializa manual para manter controle do formato
             payload = {"steps": []}
             for s in self.steps:
                 normalized_input_template = s.input_template.strip() or DEFAULT_INPUT_TEMPLATE
                 d = {
                     "key": s.key,
                     "agent_name": s.agent_name,
                     "role_desc": s.role_desc,
                     "command": s.command,
                     "instruction": s.instruction,
                 }
                 if normalized_input_template != DEFAULT_INPUT_TEMPLATE:
                     d["input_template"] = normalized_input_template
                 if s.style != "blue":
                     d["style"] = s.style
                 if s.is_code:
                     d["is_code"] = True
                 if s.timeout != 120:
                     d["timeout"] = s.timeout
                 if s.max_input_chars:
                     d["max_input_chars"] = s.max_input_chars
                 if s.max_output_chars:
                     d["max_output_chars"] = s.max_output_chars
                 if s.max_context_chars:
                     d["max_context_chars"] = s.max_context_chars
                 
                 payload["steps"].append(d)

             # Grava de forma formatada
             with open(self.config_path, "w", encoding="utf-8") as f:
                 json.dump(payload, f, indent=2, ensure_ascii=False)
                 f.write("\n") # EOF newline normal em editores
                 
             self.sub_title = str(self.config_path)
             self.notify(f"Salvo em {self.config_path}", severity="information")
             
             # DEF-04: Remover assinatura antiga se existir
             sig_path = get_signature_file_path(self.config_path)
             if sig_path.exists():
                 sig_path.unlink()
                 self.notify("⚠️ Assinatura sidecar anterior invalidada e apagada.", severity="warning", timeout=6)
                 
        except Exception as e:
             self.notify(f"Erro ao salvar: {e}", severity="error", timeout=6)

if __name__ == "__main__":
    app = FlowConfigApp()
    app.run()
