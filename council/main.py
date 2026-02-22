import sys
import os
import shlex
import logging
import json
import typer
from pathlib import Path
from typing import Literal, Optional
from typing_extensions import Annotated
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from council.audit_log import get_audit_logger, log_event
from council.ui import UI
from council.state import CouncilState, DEFAULT_MAX_CONTEXT_CHARS, MAX_CONTEXT_CHARS_ENV_VAR
from council.executor import (
    Executor,
    DEFAULT_MAX_INPUT_CHARS,
    DEFAULT_MAX_OUTPUT_CHARS,
    MAX_INPUT_CHARS_ENV_VAR,
    MAX_OUTPUT_CHARS_ENV_VAR,
)
from council.orchestrator import Orchestrator
from council.config import (
    ConfigError,
    FLOW_CONFIG_ENV_VAR,
    FLOW_CONFIG_SOURCE_CLI,
    FLOW_CONFIG_SOURCE_CWD,
    FLOW_CONFIG_SOURCE_DEFAULT,
    FLOW_CONFIG_SOURCE_ENV,
    FLOW_CONFIG_SOURCE_USER,
    FlowStep,
    ResolvedFlowConfig,
    get_default_flow_steps,
    load_flow_steps,
    resolve_flow_config,
)
from council.history_store import HistoryStore
from council.limits import read_positive_int_env
from council.flow_signature import (
    FLOW_SIGNATURE_REQUIRED_ENV_VAR,
    FlowSignatureError,
    generate_flow_signing_keypair,
    get_signature_file_path,
    sign_flow_file,
    trust_flow_public_key,
    verify_flow_signature,
)
from council.paths import get_tui_state_file_path
from council.prerequisites import (
    BinaryPrerequisiteStatus,
    evaluate_flow_prerequisites,
    find_missing_binaries,
    find_world_writable_binary_locations,
)
from council.provider_rate_limits import (
    ProviderRateLimitProbeResult,
    probe_provider_rate_limits,
)
from council.tui_state import TUIStateCryptoError, clear_tui_prompt_history, read_tui_state_passphrase

app = typer.Typer(
    help="Council - Multi-Agent System (MAS) Orquestrador via CLI",
    add_completion=False,
)
history_app = typer.Typer(help="Gerencia histórico local da TUI.")
flow_app = typer.Typer(help="Assinatura e verificação de integridade de flow.json.")
app.add_typer(history_app, name="history")
app.add_typer(flow_app, name="flow")

_SUPPORTED_PROVIDER_LIMIT_BINARIES = {"codex", "claude", "gemini"}
_PROVIDER_LIMIT_PROBE_TIMEOUT_SECONDS = 8
_DEFAULT_INPUT_TEMPLATE = "{instruction}\n\n{full_context}"


@app.callback()
def main():
    pass


def _requires_implicit_flow_confirmation(resolved_config: ResolvedFlowConfig) -> bool:
    return (
        resolved_config.source in {FLOW_CONFIG_SOURCE_CWD, FLOW_CONFIG_SOURCE_ENV}
        and resolved_config.path is not None
    )


def _implicit_flow_source_label(resolved_config: ResolvedFlowConfig) -> str:
    if resolved_config.source == FLOW_CONFIG_SOURCE_ENV:
        return FLOW_CONFIG_ENV_VAR
    return "./flow.json"


def _confirm_implicit_flow_execution(resolved_config: ResolvedFlowConfig) -> bool:
    if resolved_config.path is None or not sys.stdin.isatty():
        return False

    source_label = _implicit_flow_source_label(resolved_config)
    return typer.confirm(
        (
            f"Detectada configuração de fluxo via {source_label} em '{resolved_config.path}'. "
            "Esse arquivo pode executar comandos no host local. Deseja continuar?"
        ),
        default=False,
        show_default=True,
    )


def _ensure_flow_prerequisites(flow_steps: list[FlowStep], ui: UI) -> bool:
    statuses = evaluate_flow_prerequisites(flow_steps)
    missing = find_missing_binaries(statuses)
    if missing:
        missing_bins = ", ".join(sorted(status.binary for status in missing))
        ui.show_error(
            (
                "Pré-requisitos ausentes no PATH para executar o fluxo: "
                f"{missing_bins}. Execute 'council doctor' para diagnóstico."
            )
        )
        return False

    for status in find_world_writable_binary_locations(statuses):
        resolved_path = status.resolved_path or status.binary
        ui.console.print(
            (
                "[yellow]Aviso de segurança: binário resolvido em diretório gravável por outros "
                f"usuários: {resolved_path}[/yellow]"
            )
        )

    return True


def _describe_resolved_flow_source(resolved_config: ResolvedFlowConfig) -> str:
    if resolved_config.source == FLOW_CONFIG_SOURCE_CLI and resolved_config.path is not None:
        return f"--flow-config ({resolved_config.path})"
    if resolved_config.source == FLOW_CONFIG_SOURCE_ENV and resolved_config.path is not None:
        return f"{FLOW_CONFIG_ENV_VAR} ({resolved_config.path})"
    if resolved_config.source == FLOW_CONFIG_SOURCE_CWD and resolved_config.path is not None:
        return f"./flow.json ({resolved_config.path})"
    if resolved_config.source == FLOW_CONFIG_SOURCE_USER and resolved_config.path is not None:
        return f"configuração do usuário ({resolved_config.path})"
    if resolved_config.source == FLOW_CONFIG_SOURCE_DEFAULT:
        return "default interno"
    if resolved_config.path is not None:
        return f"{resolved_config.source} ({resolved_config.path})"
    return resolved_config.source


def _doctor_status_label_and_style(status: BinaryPrerequisiteStatus) -> tuple[str, str]:
    if not status.is_available:
        return "MISSING", "bold red"
    if status.is_world_writable_location:
        return "WARN", "bold yellow"
    return "OK", "bold green"


def _build_doctor_status_table(statuses: list[BinaryPrerequisiteStatus]) -> Table:
    table = Table(title="Diagnóstico de binários", expand=False, header_style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Binário", no_wrap=True)
    table.add_column("Caminho resolvido")
    table.add_column("Observação")

    for status in statuses:
        status_label, status_style = _doctor_status_label_and_style(status)
        resolved_path = status.resolved_path or "-"
        if not status.is_available:
            detail = "Não encontrado no PATH"
        elif status.is_world_writable_location:
            detail = "Diretório gravável por outros usuários"
        else:
            detail = "-"
        table.add_row(
            Text(f"[{status_label}]", style=status_style),
            status.binary,
            resolved_path,
            detail,
        )

    return table


def _resolve_global_runtime_limit(env_var: str, default_value: int) -> tuple[int, str]:
    configured_value = os.getenv(env_var, "").strip()
    resolved_value = read_positive_int_env(env_var, default_value)
    source = "env" if configured_value else "default"
    return resolved_value, source


def _resolve_runtime_limit_defaults() -> dict[str, tuple[int, str]]:
    max_input_chars, input_source = _resolve_global_runtime_limit(
        MAX_INPUT_CHARS_ENV_VAR,
        DEFAULT_MAX_INPUT_CHARS,
    )
    max_output_chars, output_source = _resolve_global_runtime_limit(
        MAX_OUTPUT_CHARS_ENV_VAR,
        DEFAULT_MAX_OUTPUT_CHARS,
    )
    max_context_chars, context_source = _resolve_global_runtime_limit(
        MAX_CONTEXT_CHARS_ENV_VAR,
        DEFAULT_MAX_CONTEXT_CHARS,
    )

    return {
        "max_input_chars": (max_input_chars, input_source),
        "max_output_chars": (max_output_chars, output_source),
        "max_context_chars": (max_context_chars, context_source),
    }


def _effective_limit_display(
    step_limit: int | None,
    global_limit_value: int,
    global_limit_source: str,
) -> str:
    if step_limit is not None:
        return f"{step_limit} (passo)"
    return f"{global_limit_value} ({global_limit_source})"


def _extract_binary_from_command(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "(inválido)"
    return tokens[0] if tokens else "(vazio)"


def _extract_model_from_command(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "não identificado"

    if not tokens:
        return "não identificado"

    for index, token in enumerate(tokens):
        if token == "--model" and index + 1 < len(tokens):
            model = tokens[index + 1].strip()
            if model:
                return model
        if token.startswith("--model="):
            model = token.partition("=")[2].strip()
            if model:
                return model
        if token == "-m" and index + 1 < len(tokens):
            model = tokens[index + 1].strip()
            if model:
                return model
        if token.startswith("-m="):
            model = token.partition("=")[2].strip()
            if model:
                return model

    return "padrão da CLI"


def _build_doctor_agents_model_table(
    flow_steps: list[FlowStep],
    provider_rate_limits: dict[str, ProviderRateLimitProbeResult] | None = None,
) -> Table:
    provider_rate_limits = provider_rate_limits or {}
    table = Table(title="Agentes e modelo", expand=False, header_style="bold")
    table.add_column("Passo", no_wrap=True)
    table.add_column("Agente")
    table.add_column("Binário", no_wrap=True)
    table.add_column("Modelo")
    table.add_column("Cota (provedor)")

    for step in flow_steps:
        binary = _extract_binary_from_command(step.command)
        command_model = _extract_model_from_command(step.command)
        table.add_row(
            step.key,
            step.agent_name,
            binary,
            _doctor_model_display(binary, command_model, provider_rate_limits),
            _provider_rate_limit_summary(binary, provider_rate_limits),
        )

    return table


def _doctor_model_display(
    binary: str,
    command_model: str,
    provider_rate_limits: dict[str, ProviderRateLimitProbeResult],
) -> str:
    if command_model not in {"padrão da CLI", "não identificado"}:
        return command_model
    result = provider_rate_limits.get(binary)
    if result is not None and result.model:
        return f"{result.model} (CLI)"
    return command_model


def _provider_rate_limit_summary(
    binary: str,
    provider_rate_limits: dict[str, ProviderRateLimitProbeResult],
) -> str:
    result = provider_rate_limits.get(binary)
    if result is not None:
        if result.status == "unsupported":
            return "n/a"
        return result.summary
    if binary == "codex":
        return "indisponível automaticamente; use /status"
    if binary == "claude":
        return "indisponível automaticamente; use /usage"
    if binary == "gemini":
        return "indisponível automaticamente; use /stats"
    return "n/a"


def _resolve_provider_rate_limits(flow_steps: list[FlowStep]) -> dict[str, ProviderRateLimitProbeResult]:
    binaries = {_extract_binary_from_command(step.command) for step in flow_steps}
    probe_targets = sorted(binary for binary in binaries if binary in _SUPPORTED_PROVIDER_LIMIT_BINARIES)
    if not probe_targets:
        return {}
    return probe_provider_rate_limits(
        probe_targets,
        timeout_seconds=_PROVIDER_LIMIT_PROBE_TIMEOUT_SECONDS,
    )


def _build_doctor_rate_limits_table(
    flow_steps: list[FlowStep],
    runtime_limit_defaults: dict[str, tuple[int, str]],
) -> Table:
    table = Table(title="Rate limits efetivos", expand=False, header_style="bold")
    table.add_column("Passo", no_wrap=True)
    table.add_column("Input")
    table.add_column("Output")
    table.add_column("Contexto")

    default_max_input, input_source = runtime_limit_defaults["max_input_chars"]
    default_max_output, output_source = runtime_limit_defaults["max_output_chars"]
    default_max_context, context_source = runtime_limit_defaults["max_context_chars"]

    for step in flow_steps:
        table.add_row(
            step.key,
            _effective_limit_display(step.max_input_chars, default_max_input, input_source),
            _effective_limit_display(step.max_output_chars, default_max_output, output_source),
            _effective_limit_display(step.max_context_chars, default_max_context, context_source),
        )

    return table


def _resolve_existing_file(raw_path: str, *, label: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"Arquivo não encontrado para {label}: {path}")
    if not path.is_file():
        raise typer.BadParameter(f"O caminho informado para {label} não é arquivo: {path}")
    return path


def _resolve_flow_edit_path(flow_config: str | None) -> Path | None:
    if flow_config is None:
        try:
            resolved_config = resolve_flow_config(None)
            if resolved_config.source in (FLOW_CONFIG_SOURCE_DEFAULT, FLOW_CONFIG_SOURCE_ENV):
                return None
            return resolved_config.path
        except ConfigError:
            return None
    return Path(flow_config).expanduser()


def _parse_flow_editor_name(raw_value: str, *, source: str) -> Literal["tui", "simple"]:
    normalized = raw_value.strip().lower()
    if normalized == "tui":
        return "tui"
    if normalized == "simple":
        return "simple"
    raise typer.BadParameter(
        f"Editor inválido em {source}: '{raw_value}'. Use 'tui' ou 'simple'."
    )


def _resolve_flow_editor_choice(editor: str | None) -> Literal["tui", "simple"]:
    if editor is not None:
        return _parse_flow_editor_name(editor, source="--editor")

    if not sys.stdin.isatty():
        return "tui"

    while True:
        selected = typer.prompt(
            "Escolha o editor de fluxo (tui/simple)",
            default="tui",
        )
        try:
            return _parse_flow_editor_name(selected, source="prompt")
        except typer.BadParameter as exc:
            typer.echo(str(exc))


def _load_flow_steps_for_editor(flow_path: Path | None) -> list[FlowStep]:
    if flow_path is None or not flow_path.exists():
        return get_default_flow_steps()
    try:
        return load_flow_steps(str(flow_path))
    except ConfigError as exc:
        typer.echo(f"Aviso: falha ao carregar '{flow_path}': {exc}")
        typer.echo("Editor iniciará com o fluxo default interno.")
        return get_default_flow_steps()


def _summarize_flow_steps(steps: list[FlowStep]) -> None:
    typer.echo("")
    typer.echo("Passos atuais:")
    if not steps:
        typer.echo("  (nenhum passo)")
        return
    for index, step in enumerate(steps, start=1):
        command_preview = step.command.replace("\n", " ").strip() or "(vazio)"
        if len(command_preview) > 60:
            command_preview = f"{command_preview[:57]}..."
        typer.echo(
            f"  {index}. key={step.key} | agent={step.agent_name} | command={command_preview}"
        )


def _prompt_positive_int(label: str, default: int) -> int:
    while True:
        raw_value = typer.prompt(label, default=str(default)).strip()
        try:
            parsed_value = int(raw_value)
        except ValueError:
            typer.echo(f"Valor inválido para '{label}'. Informe inteiro positivo.")
            continue
        if parsed_value <= 0:
            typer.echo(f"Valor inválido para '{label}'. Informe inteiro positivo.")
            continue
        return parsed_value


def _prompt_optional_positive_int(label: str, default: int | None) -> int | None:
    while True:
        default_text = str(default) if default is not None else ""
        raw_value = typer.prompt(label, default=default_text).strip()
        if not raw_value:
            return None
        try:
            parsed_value = int(raw_value)
        except ValueError:
            typer.echo(f"Valor inválido para '{label}'. Informe inteiro positivo ou vazio.")
            continue
        if parsed_value <= 0:
            typer.echo(f"Valor inválido para '{label}'. Informe inteiro positivo ou vazio.")
            continue
        return parsed_value


def _prompt_text_field(
    label: str,
    default: str,
    *,
    fallback: str | None = None,
    decode_newlines: bool = False,
) -> str:
    prompt_default = default.replace("\n", "\\n") if decode_newlines else default
    raw_value = typer.prompt(label, default=prompt_default).strip()
    value = raw_value.replace("\\n", "\n") if decode_newlines else raw_value
    if value:
        return value
    if fallback is not None:
        return fallback
    return default


def _prompt_step_index(total_steps: int, label: str, *, default_value: int = 1) -> int | None:
    if total_steps <= 0:
        typer.echo("Não há passos disponíveis para essa ação.")
        return None
    raw_value = typer.prompt(label, default=str(default_value)).strip()
    try:
        parsed_value = int(raw_value)
    except ValueError:
        typer.echo("Índice inválido. Informe um número inteiro.")
        return None
    if parsed_value < 1 or parsed_value > total_steps:
        typer.echo(f"Índice fora do intervalo. Use valores entre 1 e {total_steps}.")
        return None
    return parsed_value - 1


def _prompt_step_form(step: FlowStep, index: int) -> FlowStep:
    typer.echo("")
    typer.echo(f"Editando passo #{index + 1} (use Enter para manter padrão).")
    key = _prompt_text_field("key", step.key, fallback=f"step_{index + 1}")
    agent_name = _prompt_text_field("agent_name", step.agent_name, fallback="Agent")
    role_desc = _prompt_text_field("role_desc", step.role_desc, fallback="Role")
    command = _prompt_text_field("command", step.command, fallback="echo 'no command'")
    instruction = _prompt_text_field(
        "instruction (use \\n para nova linha)",
        step.instruction,
        fallback="instruction",
        decode_newlines=True,
    )
    input_template = _prompt_text_field(
        "input_template (use \\n para nova linha)",
        step.input_template,
        fallback=_DEFAULT_INPUT_TEMPLATE,
        decode_newlines=True,
    )
    style = _prompt_text_field("style", step.style, fallback="blue")
    is_code = typer.confirm("is_code?", default=step.is_code)
    timeout = _prompt_positive_int("timeout (segundos)", step.timeout)
    max_input_chars = _prompt_optional_positive_int(
        "max_input_chars (vazio = padrão)",
        step.max_input_chars,
    )
    max_output_chars = _prompt_optional_positive_int(
        "max_output_chars (vazio = padrão)",
        step.max_output_chars,
    )
    max_context_chars = _prompt_optional_positive_int(
        "max_context_chars (vazio = padrão)",
        step.max_context_chars,
    )

    return FlowStep(
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


def _new_default_step(position: int) -> FlowStep:
    return FlowStep(
        key=f"step_{position}",
        agent_name="Agent",
        role_desc="Role",
        command="codex exec --skip-git-repo-check",
        instruction="instruction",
        input_template=_DEFAULT_INPUT_TEMPLATE,
    )


def _run_simple_flow_editor_session(initial_steps: list[FlowStep]) -> tuple[list[FlowStep], bool]:
    steps = list(initial_steps)
    while True:
        _summarize_flow_steps(steps)
        action = typer.prompt(
            "Ação [e=editar, a=adicionar, r=remover, m=mover, s=salvar, q=sair]",
            default="s",
        ).strip().lower()

        if action == "e":
            index = _prompt_step_index(len(steps), "Número do passo para editar")
            if index is None:
                continue
            steps[index] = _prompt_step_form(steps[index], index)
            continue

        if action == "a":
            new_step = _prompt_step_form(_new_default_step(len(steps) + 1), len(steps))
            steps.append(new_step)
            continue

        if action == "r":
            index = _prompt_step_index(len(steps), "Número do passo para remover")
            if index is None:
                continue
            step = steps[index]
            if typer.confirm(
                f"Remover passo #{index + 1} ({step.key})?",
                default=False,
                show_default=True,
            ):
                steps.pop(index)
            continue

        if action == "m":
            if len(steps) < 2:
                typer.echo("É necessário ter pelo menos 2 passos para reordenar.")
                continue
            source_index = _prompt_step_index(
                len(steps),
                "Mover passo de qual posição?",
            )
            if source_index is None:
                continue
            target_index = _prompt_step_index(
                len(steps),
                "Mover para qual posição?",
                default_value=source_index + 1,
            )
            if target_index is None:
                continue
            moved_step = steps.pop(source_index)
            steps.insert(target_index, moved_step)
            continue

        if action == "s":
            if not steps:
                typer.echo("O fluxo precisa ter pelo menos um passo antes de salvar.")
                continue
            return steps, True

        if action == "q":
            if typer.confirm("Sair sem salvar alterações?", default=False, show_default=True):
                return steps, False
            continue

        typer.echo("Ação inválida. Use: e, a, r, m, s ou q.")


def _serialize_flow_steps(steps: list[FlowStep]) -> dict[str, list[dict[str, object]]]:
    payload: dict[str, list[dict[str, object]]] = {"steps": []}
    for step in steps:
        normalized_input_template = step.input_template.strip() or _DEFAULT_INPUT_TEMPLATE
        serialized_step: dict[str, object] = {
            "key": step.key,
            "agent_name": step.agent_name,
            "role_desc": step.role_desc,
            "command": step.command,
            "instruction": step.instruction,
        }
        if normalized_input_template != _DEFAULT_INPUT_TEMPLATE:
            serialized_step["input_template"] = normalized_input_template
        if step.style != "blue":
            serialized_step["style"] = step.style
        if step.is_code:
            serialized_step["is_code"] = True
        if step.timeout != 120:
            serialized_step["timeout"] = step.timeout
        if step.max_input_chars:
            serialized_step["max_input_chars"] = step.max_input_chars
        if step.max_output_chars:
            serialized_step["max_output_chars"] = step.max_output_chars
        if step.max_context_chars:
            serialized_step["max_context_chars"] = step.max_context_chars
        payload["steps"].append(serialized_step)
    return payload


def _save_flow_steps(flow_path: Path, steps: list[FlowStep]) -> None:
    flow_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_flow_steps(steps)
    with flow_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")

    signature_path = get_signature_file_path(flow_path)
    if signature_path.exists():
        signature_path.unlink()
        typer.echo("Aviso: assinatura sidecar anterior invalidada e apagada.")


def _resolve_save_path(flow_path: Path | None) -> Path:
    if flow_path is not None:
        return flow_path
    suggested_path = str(Path.cwd() / "flow.json")
    raw_path = typer.prompt("Salvar fluxo em", default=suggested_path).strip()
    return Path(raw_path).expanduser()


def _run_flow_edit_simple(flow_path: Path | None) -> None:
    steps = _load_flow_steps_for_editor(flow_path)
    typer.echo("Editor simples de flow.json (modo terminal).")
    typer.echo("Dica: use \\n para inserir quebra de linha em instruction/input_template.")

    updated_steps, should_save = _run_simple_flow_editor_session(steps)
    if not should_save:
        typer.echo("Edição cancelada sem salvar.")
        return

    target_path = _resolve_save_path(flow_path)
    try:
        _save_flow_steps(target_path, updated_steps)
    except OSError as exc:
        typer.echo(f"Erro ao salvar fluxo em '{target_path}': {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"Fluxo salvo em: {target_path}")


def _run_flow_edit_tui(flow_path: Path | None) -> None:
    try:
        from council.flow_tui import FlowConfigApp
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            typer.echo(
                "Dependência 'textual' não encontrada. "
                "Instale com: pip install -r requirements.txt"
            )
            raise typer.Exit(code=1)
        raise

    app = FlowConfigApp(config_path=flow_path)
    app.run()


@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="O prompt inicial / requisito do usuário para iniciar o fluxo.")],
    flow_config: Annotated[
        Optional[str],
        typer.Option(
            "--flow-config",
            "-c",
            help=(
                "Caminho para JSON com a definição de passos. "
                f"Se omitido: {FLOW_CONFIG_ENV_VAR} -> ./flow.json -> "
                "~/.config/council/flow.json -> default interno."
            ),
        ),
    ] = None,
):
    """
    Executa o loop de consenso do Council usando:
    - o fluxo padrão interno; ou
    - um fluxo customizado via JSON.
    """
    ui = UI()
    try:
        audit_logger = get_audit_logger()
    except ValueError as exc:
        ui.show_error(f"Configuração inválida de logging: {exc}")
        raise typer.Exit(code=1)
    log_event(
        audit_logger,
        "main.run.invoked",
        level=logging.INFO,
        flow_config_arg=flow_config or "",
        prompt_chars=len(prompt),
    )
    try:
        state = CouncilState()
        executor = Executor(ui)
    except ValueError as exc:
        log_event(
            audit_logger,
            "main.run.invalid_limits",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        ui.show_error(f"Configuração inválida de limites: {exc}")
        raise typer.Exit(code=1)

    try:
        resolved_config = resolve_flow_config(flow_config)
    except ConfigError as exc:
        log_event(
            audit_logger,
            "main.run.invalid_flow_config",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        ui.show_error(f"Erro ao carregar configuração do fluxo: {exc}")
        raise typer.Exit(code=1)

    if _requires_implicit_flow_confirmation(resolved_config):
        source_label = _implicit_flow_source_label(resolved_config)
        if not sys.stdin.isatty():
            log_event(
                audit_logger,
                "main.run.implicit_flow_blocked_non_interactive",
                level=logging.ERROR,
                flow_source=source_label,
                flow_path=resolved_config.path,
            )
            ui.show_error(
                "Execução bloqueada em modo não interativo: configuração de fluxo detectada via "
                f"{source_label}. Use --flow-config para confirmar explicitamente o arquivo."
            )
            raise typer.Exit(code=1)
        if not _confirm_implicit_flow_execution(resolved_config):
            log_event(
                audit_logger,
                "main.run.implicit_flow_rejected",
                level=logging.INFO,
                flow_source=source_label,
                flow_path=resolved_config.path,
            )
            ui.show_error(
                "Execução cancelada. Forneça --flow-config para confirmar explicitamente o fluxo."
            )
            raise typer.Exit(code=1)

    try:
        flow_steps = load_flow_steps(flow_config, resolved_config=resolved_config)
    except ConfigError as exc:
        log_event(
            audit_logger,
            "main.run.invalid_flow_steps",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        ui.show_error(f"Erro ao carregar configuração do fluxo: {exc}")
        raise typer.Exit(code=1)

    if not _ensure_flow_prerequisites(flow_steps, ui):
        log_event(
            audit_logger,
            "main.run.prerequisites_missing",
            level=logging.ERROR,
            planned_steps=len(flow_steps),
        )
        raise typer.Exit(code=1)

    history_store: HistoryStore | None = None
    try:
        history_store = HistoryStore()
    except OSError as exc:
        log_event(
            audit_logger,
            "main.run.history_store_unavailable",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        ui.show_error(f"Aviso: persistência estruturada indisponível: {exc}")

    # Inicia a orquestração
    log_event(
        audit_logger,
        "main.run.orchestrator_start",
        level=logging.INFO,
        flow_source=resolved_config.source,
        flow_path=str(resolved_config.path) if resolved_config.path is not None else "",
        planned_steps=len(flow_steps),
    )
    orchestrator = Orchestrator(
        state,
        executor,
        ui,
        flow_steps=flow_steps,
        history_store=history_store,
        flow_config_path=str(resolved_config.path) if resolved_config.path is not None else None,
        flow_config_source=resolved_config.source,
    )
    orchestrator.run_flow(prompt)


@app.command()
def doctor(
    flow_config: Annotated[
        Optional[str],
        typer.Option(
            "--flow-config",
            "-c",
            help=(
                "Caminho para JSON com a definição de passos a validar. "
                f"Se omitido: {FLOW_CONFIG_ENV_VAR} -> ./flow.json -> "
                "~/.config/council/flow.json -> default interno."
            ),
        ),
    ] = None,
) -> None:
    """
    Diagnostica pré-requisitos de binários para o fluxo atual.
    """
    try:
        audit_logger = get_audit_logger()
    except ValueError as exc:
        typer.echo(f"Configuração inválida de logging: {exc}")
        raise typer.Exit(code=1)

    log_event(
        audit_logger,
        "main.doctor.invoked",
        level=logging.INFO,
        flow_config_arg=flow_config or "",
    )

    try:
        resolved_config = resolve_flow_config(flow_config)
        flow_steps = load_flow_steps(flow_config, resolved_config=resolved_config)
    except ConfigError as exc:
        log_event(
            audit_logger,
            "main.doctor.invalid_flow_config",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        typer.echo(f"Erro ao carregar configuração do fluxo: {exc}")
        raise typer.Exit(code=1)

    console = Console()
    try:
        runtime_limit_defaults = _resolve_runtime_limit_defaults()
    except ValueError as exc:
        log_event(
            audit_logger,
            "main.doctor.invalid_limits",
            level=logging.ERROR,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        console.print(
            Panel(
                f"Configuração inválida de limites: {exc}",
                title="[bold red]Falha[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)

    statuses = evaluate_flow_prerequisites(flow_steps)
    missing = find_missing_binaries(statuses)
    world_writable = find_world_writable_binary_locations(statuses)
    provider_rate_limits: dict[str, ProviderRateLimitProbeResult]
    try:
        provider_rate_limits = _resolve_provider_rate_limits(flow_steps)
    except Exception as exc:
        provider_rate_limits = {}
        log_event(
            audit_logger,
            "main.doctor.provider_limit_probe_failed",
            level=logging.WARNING,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    flow_source_description = _describe_resolved_flow_source(resolved_config)
    console.print(
        Panel.fit(
            f"Fonte do fluxo: {flow_source_description}",
            title="[bold cyan]Council Doctor[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print(_build_doctor_agents_model_table(flow_steps, provider_rate_limits))
    console.print(_build_doctor_rate_limits_table(flow_steps, runtime_limit_defaults))
    if not statuses:
        log_event(
            audit_logger,
            "main.doctor.no_commands",
            level=logging.INFO,
            flow_source=resolved_config.source,
        )
        console.print(
            Panel(
                "Nenhum comando encontrado no fluxo.",
                title="[bold yellow]Aviso[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        return

    console.print(_build_doctor_status_table(statuses))
    safe_total = len(
        [
            status
            for status in statuses
            if status.is_available and not status.is_world_writable_location
        ]
    )
    summary_border = "red" if missing else ("yellow" if world_writable else "green")
    console.print(
        Panel(
            (
                f"Total verificado: {len(statuses)}\n"
                f"OK: {safe_total}\n"
                f"Avisos: {len(world_writable)}\n"
                f"Ausentes: {len(missing)}"
            ),
            title="[bold]Resumo[/bold]",
            border_style=summary_border,
            expand=False,
        )
    )

    if world_writable:
        log_event(
            audit_logger,
            "main.doctor.world_writable_warning",
            level=logging.WARNING,
            risky_binaries=sorted(status.binary for status in world_writable),
        )
        console.print(
            Panel(
                (
                    "Foram detectados binários em diretórios graváveis por outros usuários. "
                    "Revise os avisos acima."
                ),
                title="[bold yellow]Atenção de segurança[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )

    if missing:
        missing_bins = ", ".join(sorted(status.binary for status in missing))
        log_event(
            audit_logger,
            "main.doctor.prerequisites_missing",
            level=logging.ERROR,
            missing_binaries=sorted(status.binary for status in missing),
            total_binaries=len(statuses),
        )
        console.print(
            Panel(
                f"Pré-requisitos ausentes no PATH: {missing_bins}.",
                title="[bold red]Falha[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)

    log_event(
        audit_logger,
        "main.doctor.success",
        level=logging.INFO,
        total_binaries=len(statuses),
    )
    console.print(
        Panel(
            "Pré-requisitos atendidos.",
            title="[bold green]Sucesso[/bold green]",
            border_style="green",
            expand=False,
        )
    )


@app.command()
def tui(
    prompt: Annotated[
        Optional[str],
        typer.Option(
            "--prompt",
            "-p",
            help="Prompt inicial opcional para já abrir preenchido na TUI.",
        ),
    ] = None,
    flow_config: Annotated[
        Optional[str],
        typer.Option(
            "--flow-config",
            "-c",
            help=(
                "Caminho de configuração opcional para já abrir preenchido na TUI. "
                f"Se vazio, a execução seguirá a ordem: {FLOW_CONFIG_ENV_VAR} -> "
                "./flow.json -> ~/.config/council/flow.json -> default interno."
            ),
        ),
    ] = None,
):
    """
    Inicia a interface TUI (Textual) do Council.
    """
    try:
        audit_logger = get_audit_logger()
    except ValueError as exc:
        typer.echo(f"Configuração inválida de logging: {exc}")
        raise typer.Exit(code=1)

    log_event(
        audit_logger,
        "main.tui.invoked",
        level=logging.INFO,
        flow_config_arg=flow_config or "",
        has_initial_prompt=bool(prompt),
    )

    try:
        from council.tui import run_tui
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            typer.echo(
                "Dependência 'textual' não encontrada. "
                "Instale com: pip install -r requirements.txt"
            )
            raise typer.Exit(code=1)
        raise

    run_tui(initial_prompt=prompt or "", initial_flow_config=flow_config or "")


@flow_app.command("keygen")
def flow_keygen(
    key_id: Annotated[
        str,
        typer.Option(
            "--key-id",
            help="Identificador da chave (ex.: equipe-seguranca-v1).",
        ),
    ],
    private_key: Annotated[
        Optional[str],
        typer.Option(
            "--private-key",
            "-k",
            help="Caminho de saída para a chave privada PEM. Padrão: <key-id>.key.pem",
        ),
    ] = None,
    public_key: Annotated[
        Optional[str],
        typer.Option(
            "--public-key",
            "-u",
            help="Caminho de saída para a chave pública PEM. Padrão: <key-id>.pub.pem",
        ),
    ] = None,
    trust: Annotated[
        bool,
        typer.Option(
            "--trust",
            help="Registra automaticamente a chave pública no trust store local.",
        ),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Permite sobrescrever arquivos de chave já existentes.",
        ),
    ] = False,
) -> None:
    """
    Gera par de chaves Ed25519 para assinatura de flow.json.
    """
    normalized_key_id = key_id.strip()
    if not normalized_key_id:
        raise typer.BadParameter("Informe um valor não vazio para --key-id.")

    private_key_path = (
        Path(private_key).expanduser()
        if private_key
        else Path(f"{normalized_key_id}.key.pem")
    )
    public_key_path = (
        Path(public_key).expanduser()
        if public_key
        else Path(f"{normalized_key_id}.pub.pem")
    )

    try:
        generate_flow_signing_keypair(
            private_key_path=private_key_path,
            public_key_path=public_key_path,
            overwrite=overwrite,
        )
        trusted_path: Path | None = None
        if trust:
            trusted_path = trust_flow_public_key(
                public_key_path=public_key_path,
                key_id=normalized_key_id,
                overwrite=overwrite,
            )
    except FlowSignatureError as exc:
        typer.echo(f"Falha ao gerar chaves de assinatura: {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"Chave privada gerada em: {private_key_path}")
    typer.echo(f"Chave pública gerada em: {public_key_path}")
    if trusted_path is not None:
        typer.echo(f"Chave pública confiada em: {trusted_path}")


@flow_app.command("sign")
def flow_sign(
    flow_config: Annotated[
        str,
        typer.Argument(help="Caminho para o flow.json a ser assinado."),
    ],
    private_key: Annotated[
        str,
        typer.Option(
            "--private-key",
            "-k",
            help="Caminho para a chave privada PEM do autor.",
        ),
    ],
    key_id: Annotated[
        str,
        typer.Option(
            "--key-id",
            help="Identificador da chave (deve corresponder ao trust store de quem verifica).",
        ),
    ],
    signature_file: Annotated[
        Optional[str],
        typer.Option(
            "--signature-file",
            "-s",
            help="Caminho do arquivo de assinatura. Padrão: <flow>.sig",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Permite sobrescrever assinatura existente.",
        ),
    ] = False,
) -> None:
    """
    Assina um flow.json e gera sidecar .sig.
    """
    flow_path = _resolve_existing_file(flow_config, label="flow_config")
    private_key_path = _resolve_existing_file(private_key, label="chave privada")
    signature_path = Path(signature_file).expanduser() if signature_file else None

    try:
        written_signature_path = sign_flow_file(
            flow_path=flow_path,
            private_key_path=private_key_path,
            key_id=key_id,
            signature_path=signature_path,
            overwrite=overwrite,
        )
    except FlowSignatureError as exc:
        typer.echo(f"Falha ao assinar fluxo: {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"Assinatura criada: {written_signature_path}")
    typer.echo(
        (
            f"Para bloquear fluxos sem assinatura válida em runtime, defina "
            f"{FLOW_SIGNATURE_REQUIRED_ENV_VAR}=1."
        )
    )


@flow_app.command("edit")
def flow_edit(
    flow_config: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Caminho para o flow.json a editar. "
                "Se omitido ou não encontrado, inicia modelo default interno."
            ),
        ),
    ] = None,
    editor: Annotated[
        Optional[str],
        typer.Option(
            "--editor",
            "-e",
            help=(
                "Editor usado para edição de fluxo: "
                "'tui' (Textual) ou 'simple' (prompt no terminal). "
                "Se omitido, pergunta em modo interativo."
            ),
        ),
    ] = None,
) -> None:
    """
    Inicia a edição de flow.json, com escolha de editor (TUI ou terminal simples).
    """
    flow_path = _resolve_flow_edit_path(flow_config)
    selected_editor = _resolve_flow_editor_choice(editor)
    if selected_editor == "simple":
        _run_flow_edit_simple(flow_path)
        return
    _run_flow_edit_tui(flow_path)


@flow_app.command("trust")
def flow_trust(
    public_key: Annotated[
        str,
        typer.Argument(help="Arquivo PEM da chave pública a confiar."),
    ],
    key_id: Annotated[
        str,
        typer.Option("--key-id", help="Identificador da chave no trust store local."),
    ],
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Permite substituir chave confiada existente para o mesmo key_id.",
        ),
    ] = False,
) -> None:
    """
    Registra uma chave pública no trust store local do Council.
    """
    public_key_path = _resolve_existing_file(public_key, label="chave pública")
    try:
        trusted_path = trust_flow_public_key(
            public_key_path=public_key_path,
            key_id=key_id,
            overwrite=overwrite,
        )
    except FlowSignatureError as exc:
        typer.echo(f"Falha ao confiar chave pública: {exc}")
        raise typer.Exit(code=1)

    typer.echo(f"Chave confiada com sucesso em: {trusted_path}")


@flow_app.command("verify")
def flow_verify(
    flow_config: Annotated[
        str,
        typer.Argument(help="Caminho para o flow.json a validar."),
    ],
    signature_file: Annotated[
        Optional[str],
        typer.Option(
            "--signature-file",
            "-s",
            help="Arquivo de assinatura. Padrão: <flow>.sig",
        ),
    ] = None,
    public_key: Annotated[
        Optional[str],
        typer.Option(
            "--public-key",
            "-u",
            help="Arquivo PEM explícito para verificação (bypass do trust store).",
        ),
    ] = None,
) -> None:
    """
    Verifica assinatura de flow.json contra trust store local ou chave explícita.
    """
    flow_path = _resolve_existing_file(flow_config, label="flow_config")
    signature_path = (
        Path(signature_file).expanduser()
        if signature_file
        else get_signature_file_path(flow_path)
    )
    public_key_path = (
        _resolve_existing_file(public_key, label="chave pública")
        if public_key is not None
        else None
    )

    try:
        verify_flow_signature(
            flow_path=flow_path,
            signature_path=signature_path,
            public_key_path=public_key_path,
            require_signature=True,
        )
    except FlowSignatureError as exc:
        typer.echo(f"Falha na verificação da assinatura: {exc}")
        raise typer.Exit(code=1)

    verification_scope = "chave pública informada via --public-key"
    if public_key_path is None:
        verification_scope = "trust store local"
    typer.echo(
        (
            f"Assinatura válida para '{flow_path}' usando '{signature_path}' "
            f"({verification_scope})."
        )
    )


@history_app.command("clear")
def history_clear() -> None:
    """
    Limpa o histórico de prompts persistido pela TUI.
    """
    state_path = get_tui_state_file_path()
    try:
        passphrase = read_tui_state_passphrase() or None
    except TUIStateCryptoError as exc:
        typer.echo(f"Falha ao resolver passphrase para limpeza: {exc}")
        raise typer.Exit(code=1)

    try:
        cleared = clear_tui_prompt_history(state_path, passphrase=passphrase)
    except (OSError, TUIStateCryptoError) as exc:
        typer.echo(f"Falha ao limpar histórico em '{state_path}': {exc}")
        raise typer.Exit(code=1)

    if not cleared:
        typer.echo(f"Nenhum histórico encontrado em '{state_path}'.")
        return

    typer.echo(f"Histórico de prompts removido de '{state_path}'.")


@history_app.command("runs")
def history_runs(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Quantidade máxima de runs retornados."),
    ] = 20,
) -> None:
    """
    Lista runs persistidos no banco local.
    """
    if limit <= 0:
        typer.echo("Valor inválido para --limit: informe um inteiro positivo.")
        raise typer.Exit(code=1)

    try:
        history_store = HistoryStore()
    except OSError as exc:
        typer.echo(f"Falha ao abrir histórico de runs: {exc}")
        raise typer.Exit(code=1)

    runs = history_store.list_runs(limit=limit)
    if not runs:
        typer.echo("Nenhum run persistido em COUNCIL_HOME/db/history.sqlite3.")
        return

    for run in runs:
        run_id = run.get("id")
        status = run.get("status")
        started_at = run.get("started_at_utc")
        duration_ms = run.get("duration_ms")
        executed_steps = run.get("executed_steps")
        successful_steps = run.get("successful_steps")
        source = run.get("flow_config_source") or "default"
        typer.echo(
            (
                f"run={run_id} status={status} started_at={started_at} "
                f"duration_ms={duration_ms} steps={successful_steps}/{executed_steps} "
                f"flow_source={source}"
            )
        )

def cli():
    app()

if __name__ == "__main__":
    app()
