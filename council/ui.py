from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live
from contextlib import contextmanager

class UI:
    def __init__(self):
        self.console = Console()

    @contextmanager
    def spinner(self, text: str):
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=True
        ) as progress:
            progress.add_task(description=text, total=None)
            yield

    @contextmanager
    def live_stream(self, title: str, style: str = "blue", max_height: int = 10):
        """
        Gera um painel com altura fixa que apaga automaticamente após o término.
        Retorna uma função callback para adicionar novas linhas ao console.
        """
        content_lines = []
        
        def update_content(new_text: str):
            content_lines.append(new_text)
            # Mantém apenas as últimas N linhas para não estourar o limite de altura
            visible_lines = content_lines[-(max_height - 2):]
            renderable = "\n".join(visible_lines)
            live.update(
                Panel(
                    renderable,
                    title=f"[bold {style}]{title}[/bold {style}]",
                    border_style=style,
                    height=max_height,
                    expand=False
                )
            )

        # Usando transient=True o painel desaparecerá magicamente ao concluir
        with Live(
            Panel("Inicializando processamento...", title=f"[bold {style}]{title}[/bold {style}]", border_style=style, height=max_height, expand=False), 
            console=self.console, 
            transient=True, 
            refresh_per_second=10
        ) as live:
            yield update_content

    def show_panel(self, title: str, content: str, style: str = "blue", is_code: bool = False, language: str = "python") -> None:
        """
        Exibe um painel Rich contendo o texto, com formatação Syntax se is_code=True.
        """
        if is_code:
            renderable = Syntax(content, language, theme="monokai", word_wrap=True)
        else:
            renderable = content
            
        self.console.print(
            Panel(
                renderable,
                title=f"[bold {style}]{title}[/bold {style}]",
                border_style=style,
                expand=False
            )
        )

    def show_error(self, message: str) -> None:
        self.console.print(
            Panel(message, title="[bold red]Erro[/bold red]", border_style="red", expand=False)
        )
        
    def show_success(self, message: str) -> None:
        self.console.print(
            Panel(message, title="[bold green]Sucesso[/bold green]", border_style="green", expand=False)
        )
