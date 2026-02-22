import sys
import logging
import typer
from pathlib import Path
from typing import Optional
from typing_extensions import Annotated

from council.audit_log import get_audit_logger, log_event
from council.ui import UI
from council.state import CouncilState
from council.executor import Executor
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
    load_flow_steps,
    resolve_flow_config,
)
from council.history_store import HistoryStore
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
from council.tui_state import TUIStateCryptoError, clear_tui_prompt_history, read_tui_state_passphrase

app = typer.Typer(
    help="Council - Multi-Agent System (MAS) Orquestrador via CLI",
    add_completion=False,
)
history_app = typer.Typer(help="Gerencia histórico local da TUI.")
flow_app = typer.Typer(help="Assinatura e verificação de integridade de flow.json.")
app.add_typer(history_app, name="history")
app.add_typer(flow_app, name="flow")

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


def _render_doctor_status_line(status: BinaryPrerequisiteStatus) -> str:
    if not status.is_available:
        return f"[MISSING] {status.binary}: não encontrado no PATH"

    resolved_path = status.resolved_path or "(caminho desconhecido)"
    if status.is_world_writable_location:
        return f"[WARN] {status.binary}: {resolved_path} (diretório gravável por outros usuários)"
    return f"[OK] {status.binary}: {resolved_path}"


def _resolve_existing_file(raw_path: str, *, label: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"Arquivo não encontrado para {label}: {path}")
    if not path.is_file():
        raise typer.BadParameter(f"O caminho informado para {label} não é arquivo: {path}")
    return path


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

    statuses = evaluate_flow_prerequisites(flow_steps)
    missing = find_missing_binaries(statuses)
    world_writable = find_world_writable_binary_locations(statuses)

    typer.echo(f"Fonte do fluxo: {_describe_resolved_flow_source(resolved_config)}")
    if not statuses:
        log_event(
            audit_logger,
            "main.doctor.no_commands",
            level=logging.INFO,
            flow_source=resolved_config.source,
        )
        typer.echo("Nenhum comando encontrado no fluxo.")
        return

    for status in statuses:
        typer.echo(_render_doctor_status_line(status))

    if world_writable:
        log_event(
            audit_logger,
            "main.doctor.world_writable_warning",
            level=logging.WARNING,
            risky_binaries=sorted(status.binary for status in world_writable),
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
        typer.echo(f"Pré-requisitos ausentes no PATH: {missing_bins}.")
        raise typer.Exit(code=1)

    log_event(
        audit_logger,
        "main.doctor.success",
        level=logging.INFO,
        total_binaries=len(statuses),
    )
    typer.echo("Pré-requisitos atendidos.")


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
