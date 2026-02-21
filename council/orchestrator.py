from council.state import CouncilState
from council.executor import Executor, CommandError
from council.ui import UI
from council.config import FlowStep, ConfigError, get_default_flow_steps, render_step_input

class Orchestrator:
    """Responsável por controlar o fluxo de execução entre os modelos/LLMs."""
    def __init__(
        self,
        state: CouncilState,
        executor: Executor,
        ui: UI,
        flow_steps: list[FlowStep] | None = None,
    ):
        self.state = state
        self.executor = executor
        self.ui = ui
        self.flow_steps = flow_steps or get_default_flow_steps()

    def run_flow(self, user_prompt: str):
        """Dispara todas as etapas (Planejamento, Crítica, Consolidação, Impl. e Revisão)"""
        self.state.add_turn("Human", "user", user_prompt, "Requisito Inicial")
        self.ui.show_panel("Request (User)", user_prompt, style="cyan")

        try:
            step_outputs: dict[str, str] = {}
            last_output = ""

            for step in self.flow_steps:
                template_context = {
                    "user_prompt": user_prompt,
                    "full_context": self.state.get_full_context(),
                    "last_output": last_output,
                    "instruction": step.instruction,
                    **step_outputs,
                }

                input_data = render_step_input(step, template_context)

                result = self._step(
                    agent_name=step.agent_name,
                    role_desc=step.role_desc,
                    command=step.command,
                    input_data=input_data,
                    style=step.style,
                    is_code=step.is_code,
                )
                step_outputs[step.key] = result
                last_output = result
            
            self.ui.show_success("Orquestração multimodelo do Council finalizada com sucesso!")

        except CommandError:
            self.ui.show_error("O fluxo foi interrompido e etapas subsequentes foram abortadas.")
        except ConfigError as exc:
            self.ui.show_error(f"Configuração inválida do fluxo: {exc}")
            
    def _step(
        self,
        agent_name: str,
        role_desc: str,
        command: str,
        input_data: str,
        style: str,
        is_code: bool = False,
    ) -> str:
        self.ui.console.print(f"\n[bold {style}]Iniciando passo:[/bold {style}] {agent_name} ({role_desc}) via {command}")
        
        with self.ui.live_stream(f"Processando {agent_name}...", style=style) as update_cb:
            result = self.executor.run_cli(command, input_data, on_output=update_cb)
        
        self.state.add_turn(agent_name, "assistant", result, role_desc)
        
        # Limpa blocos markdown apenas na hora de exibir visualmente se for is_code
        result_display = result
        if is_code:
            lines = result.split("\n")
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            result_display = "\n".join(lines).strip()
            
        self.ui.show_panel(f"{agent_name} - {role_desc}", result_display, style=style, is_code=is_code)
        
        return result
