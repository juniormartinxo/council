import typer
from typing_extensions import Annotated

from council.ui import UI
from council.state import CouncilState
from council.executor import Executor
from council.orchestrator import Orchestrator

app = typer.Typer(
    help="Council - Multi-Agent System (MAS) Orquestrador via CLI",
    add_completion=False,
)

@app.callback()
def main():
    pass

@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="O prompt inicial / requisito do usuário para iniciar o fluxo.")]
):
    """
    Executa o loop de consenso do Council:
    1. Planejamento (Claude)
    2. Crítica (Gemini)
    3. Consolidação (Claude)
    4. Implementação (Codex)
    5. Revisão (Gemini)
    """
    ui = UI()
    state = CouncilState()
    executor = Executor(ui)
    
    # Inicia a orquestração
    orchestrator = Orchestrator(state, executor, ui)
    orchestrator.run_flow(prompt)

def cli():
    app()

if __name__ == "__main__":
    app()
