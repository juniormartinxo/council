import typer
from typing import Optional
from typing_extensions import Annotated

from council.ui import UI
from council.state import CouncilState
from council.executor import Executor
from council.orchestrator import Orchestrator
from council.config import ConfigError, FLOW_CONFIG_ENV_VAR, load_flow_steps

app = typer.Typer(
    help="Council - Multi-Agent System (MAS) Orquestrador via CLI",
    add_completion=False,
)

@app.callback()
def main():
    pass

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
                f"Se omitido, usa o default ou a env var {FLOW_CONFIG_ENV_VAR}."
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
    state = CouncilState()
    executor = Executor(ui)

    try:
        flow_steps = load_flow_steps(flow_config)
    except ConfigError as exc:
        ui.show_error(f"Erro ao carregar configuração do fluxo: {exc}")
        raise typer.Exit(code=1)

    # Inicia a orquestração
    orchestrator = Orchestrator(state, executor, ui, flow_steps=flow_steps)
    orchestrator.run_flow(prompt)

def cli():
    app()

if __name__ == "__main__":
    app()
