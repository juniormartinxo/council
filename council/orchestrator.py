from council.state import CouncilState
from council.executor import Executor, CommandError
from council.ui import UI

class Orchestrator:
    """Responsável por controlar o fluxo de execução entre os modelos/LLMs."""
    def __init__(self, state: CouncilState, executor: Executor, ui: UI):
        self.state = state
        self.executor = executor
        self.ui = ui

    def run_flow(self, user_prompt: str):
        """Dispara todas as etapas (Planejamento, Crítica, Consolidação, Impl. e Revisão)"""
        self.state.add_turn("Human", "user", user_prompt, "Requisito Inicial")
        self.ui.show_panel("Request (User)", user_prompt, style="cyan")

        try:
            # 1. Planejamento
            plan = self._step(
                agent_name="Claude",
                role_desc="Planejamento",
                command="claude -p",
                instruction="Você é um arquiteto de software. Crie um plano detalhado para o seguinte requisito:",
                input_data=self.state.get_full_context(),
                style="dark_goldenrod"
            )

            # 2. Crítica
            critique_instruction = (
                "Analise o seguinte plano de arquitetura. "
                "Aponte falhas de arquitetura e possíveis problemas de segurança:"
            )
            critique = self._step(
                agent_name="Gemini",
                role_desc="Crítica",
                command='gemini -p ""',
                instruction=critique_instruction,
                input_data=f"{critique_instruction}\n\nPLANO PROPOSTO:\n{plan}",
                style="dodger_blue1"
            )

            # 3. Consolidação
            consolidation_instruction = (
                "O plano inicial recebeu as seguintes críticas. "
                "Consolide, resolva os problemas e gere o plano final corrigido:"
            )
            final_plan = self._step(
                agent_name="Claude",
                role_desc="Consolidação",
                command="claude -p",
                instruction=consolidation_instruction,
                input_data=f"PLANO INICIAL:\n{plan}\n\nCRÍTICAS RECEBIDAS:\n{critique}\n\n{consolidation_instruction}",
                style="dark_goldenrod"
            )

            # 4. Implementação
            implementation_instruction = (
                "Você é um engenheiro de software sênior. Implemente o código conforme o seguinte plano. "
                "RETORNE APENAS O CÓDIGO FONTE FINAL E MAIS NADA, sem explicações em texto."
            )
            code = self._step(
                agent_name="Codex",
                role_desc="Implementação",
                command="codex exec --skip-git-repo-check",
                instruction=implementation_instruction,
                input_data=f"{implementation_instruction}\n\nPLANO FINAL:\n{final_plan}",
                style="bright_black",
                is_code=True
            )

            # 5. Revisão Final
            review_instruction = (
                "Você é um revisor de código rigoroso. Faça um code review detalhado do código a seguir, "
                "apontando boas práticas, bugs ocultos, problemas de segurança ou pontos de melhoria:"
            )
            self._step(
                agent_name="Gemini",
                role_desc="Revisão Final",
                command='gemini -p ""',
                instruction=review_instruction,
                input_data=f"{review_instruction}\n\nCÓDIGO:\n{code}",
                style="dodger_blue1"
            )
            
            self.ui.show_success("Orquestração multimodelo do Council finalizada com sucesso!")

        except CommandError:
            self.ui.show_error("O fluxo foi interrompido e etapas subsequentes foram abortadas.")
            
    def _step(self, agent_name: str, role_desc: str, command: str, instruction: str, input_data: str, style: str, is_code: bool = False) -> str:
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
