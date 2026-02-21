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
                    step_key=step.key,
                    agent_name=step.agent_name,
                    role_desc=step.role_desc,
                    command=step.command,
                    input_data=input_data,
                    style=step.style,
                    is_code=step.is_code,
                )

                result = self._collect_human_feedback_loop(step, result)

                step_outputs[step.key] = result
                last_output = result
            
            self.ui.show_success("Orquestração multimodelo do Council finalizada com sucesso!")

        except CommandError:
            self.ui.show_error("O fluxo foi interrompido e etapas subsequentes foram abortadas.")
        except ConfigError as exc:
            self.ui.show_error(f"Configuração inválida do fluxo: {exc}")
            
    def _step(
        self,
        step_key: str,
        agent_name: str,
        role_desc: str,
        command: str,
        input_data: str,
        style: str,
        is_code: bool = False,
    ) -> str:
        set_active_step = getattr(self.ui, "set_active_step", None)
        if callable(set_active_step):
            set_active_step(step_key=step_key, label=f"{agent_name} ({role_desc})")

        self.ui.console.print(f"\nIniciando passo: {agent_name} ({role_desc})")
        
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

    def _collect_human_feedback_loop(self, step: FlowStep, current_output: str) -> str:
        """
        Se a UI suportar interação humana por etapa, pausa o pipeline e permite
        que o usuário:
        - continue para o próximo agente; ou
        - envie feedback para reexecutar o agente atual com ajustes.
        """
        request_feedback = getattr(self.ui, "request_step_feedback", None)
        if not callable(request_feedback):
            return current_output

        output = current_output

        while True:
            feedback = request_feedback(
                agent_name=step.agent_name,
                role_desc=step.role_desc,
                output=output,
            )
            if not feedback:
                return output

            self.state.add_turn(
                "Human",
                "user",
                feedback,
                f"Feedback para {step.agent_name} ({step.role_desc})",
            )

            follow_up_input = self._build_follow_up_input(step, previous_output=output, feedback=feedback)
            output = self._step(
                step_key=step.key,
                agent_name=step.agent_name,
                role_desc=f"{step.role_desc} (Ajuste)",
                command=step.command,
                input_data=follow_up_input,
                style=step.style,
                is_code=step.is_code,
            )

    def _build_follow_up_input(self, step: FlowStep, previous_output: str, feedback: str) -> str:
        return (
            "Você recebeu feedback do usuário sobre sua resposta anterior.\n"
            "Atualize e melhore sua resposta com base nesse feedback.\n\n"
            f"INSTRUÇÃO ORIGINAL:\n{step.instruction}\n\n"
            f"RESPOSTA ANTERIOR:\n{previous_output}\n\n"
            f"FEEDBACK DO USUÁRIO:\n{feedback}\n\n"
            "Retorne a nova versão completa da resposta."
        )
