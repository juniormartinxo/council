from time import perf_counter

from council.state import CouncilState
from council.executor import Executor, CommandError, ExecutionAborted
from council.ui import UI
from council.config import FlowStep, ConfigError, get_default_flow_steps, render_step_input
from council.history_store import HistoryStore, utc_now_iso

class Orchestrator:
    """Responsável por controlar o fluxo de execução entre os modelos/LLMs."""
    def __init__(
        self,
        state: CouncilState,
        executor: Executor,
        ui: UI,
        flow_steps: list[FlowStep] | None = None,
        history_store: HistoryStore | None = None,
        flow_config_path: str | None = None,
        flow_config_source: str | None = None,
    ):
        self.state = state
        self.executor = executor
        self.ui = ui
        self.flow_steps = flow_steps or get_default_flow_steps()
        self.history_store = history_store
        self.flow_config_path = flow_config_path
        self.flow_config_source = flow_config_source
        self._active_run_id: int | None = None
        self._history_store_available = history_store is not None
        self._history_store_warning_emitted = False
        self._step_sequence = 0
        self._executed_steps = 0
        self._successful_steps = 0

    def run_flow(self, user_prompt: str):
        """Dispara todas as etapas (Planejamento, Crítica, Consolidação, Impl. e Revisão)"""
        self.state.add_turn("Human", "user", user_prompt, "Requisito Inicial")
        self.ui.show_panel("Request (User)", user_prompt, style="cyan")
        self._step_sequence = 0
        self._executed_steps = 0
        self._successful_steps = 0
        self._active_run_id = self._open_history_run(user_prompt=user_prompt)
        run_started_perf = perf_counter()
        flow_status = "success"
        flow_error_message: str | None = None

        try:
            step_outputs: dict[str, str] = {}
            last_output = ""

            for step in self.flow_steps:
                template_context = {
                    "user_prompt": user_prompt,
                    "full_context": self.state.get_full_context(max_chars=step.max_context_chars),
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
                    timeout=step.timeout,
                    max_input_chars=step.max_input_chars,
                    max_output_chars=step.max_output_chars,
                    max_context_chars=step.max_context_chars,
                    style=step.style,
                    is_code=step.is_code,
                    is_feedback=False,
                )

                result = self._collect_human_feedback_loop(step, result)

                step_outputs[step.key] = result
                last_output = result
            
            self.ui.show_success("Orquestração multimodelo do Council finalizada com sucesso!")

        except ExecutionAborted:
            flow_status = "aborted"
            flow_error_message = "Fluxo abortado pelo usuário."
            self.ui.show_error("Fluxo abortado pelo usuário.")
        except CommandError:
            flow_status = "error"
            flow_error_message = "Falha de execução em um ou mais passos."
            self.ui.show_error("O fluxo foi interrompido e etapas subsequentes foram abortadas.")
        except ConfigError as exc:
            flow_status = "error"
            flow_error_message = f"Configuração inválida do fluxo: {exc}"
            self.ui.show_error(f"Configuração inválida do fluxo: {exc}")
        finally:
            run_duration_ms = int((perf_counter() - run_started_perf) * 1000)
            self._close_history_run(
                status=flow_status,
                error_message=flow_error_message,
                duration_ms=run_duration_ms,
            )
            self._active_run_id = None
            
    def _step(
        self,
        step_key: str,
        agent_name: str,
        role_desc: str,
        command: str,
        input_data: str,
        timeout: int,
        max_input_chars: int | None,
        max_output_chars: int | None,
        max_context_chars: int | None,
        style: str,
        is_code: bool = False,
        is_feedback: bool = False,
    ) -> str:
        self._step_sequence += 1
        sequence = self._step_sequence
        step_started_utc = utc_now_iso()
        step_started_perf = perf_counter()
        step_status = "success"
        step_error_message: str | None = None
        result = ""
        set_active_step = getattr(self.ui, "set_active_step", None)
        if callable(set_active_step):
            set_active_step(step_key=step_key, label=f"{agent_name} ({role_desc})")

        self.ui.console.print(f"\nIniciando passo: {agent_name} ({role_desc})")

        try:
            with self.ui.live_stream(f"Processando {agent_name}...", style=style) as update_cb:
                result = self.executor.run_cli(
                    command,
                    input_data,
                    timeout=timeout,
                    on_output=update_cb,
                    max_input_chars=max_input_chars,
                    max_output_chars=max_output_chars,
                )

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
            self._successful_steps += 1
            return result
        except ExecutionAborted as exc:
            step_status = "aborted"
            step_error_message = str(exc)
            raise
        except CommandError as exc:
            step_status = "error"
            step_error_message = str(exc)
            raise
        finally:
            self._executed_steps += 1
            step_finished_utc = utc_now_iso()
            step_duration_ms = int((perf_counter() - step_started_perf) * 1000)
            self._record_step_history(
                sequence=sequence,
                step_key=step_key,
                agent_name=agent_name,
                role_desc=role_desc,
                command=command,
                input_data=input_data,
                output_data=result,
                status=step_status,
                error_message=step_error_message,
                timeout_seconds=timeout,
                max_input_chars=max_input_chars,
                max_output_chars=max_output_chars,
                max_context_chars=max_context_chars,
                is_feedback=is_feedback,
                started_at_utc=step_started_utc,
                finished_at_utc=step_finished_utc,
                duration_ms=step_duration_ms,
            )

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
                timeout=step.timeout,
                max_input_chars=step.max_input_chars,
                max_output_chars=step.max_output_chars,
                max_context_chars=step.max_context_chars,
                style=step.style,
                is_code=step.is_code,
                is_feedback=True,
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

    def _open_history_run(self, user_prompt: str) -> int | None:
        if self.history_store is None or not self._history_store_available:
            return None
        return self._safe_history_call(
            operation_label="abrir run",
            callback=lambda: self.history_store.start_run(
                prompt=user_prompt,
                flow_config_path=self.flow_config_path,
                flow_config_source=self.flow_config_source,
                planned_steps=len(self.flow_steps),
            ),
        )

    def _close_history_run(self, status: str, error_message: str | None, duration_ms: int) -> None:
        run_id = self._active_run_id
        if run_id is None or self.history_store is None or not self._history_store_available:
            return
        self._safe_history_call(
            operation_label="finalizar run",
            callback=lambda: self.history_store.finish_run(
                run_id=run_id,
                status=status,
                error_message=error_message,
                executed_steps=self._executed_steps,
                successful_steps=self._successful_steps,
                duration_ms=duration_ms,
            ),
        )

    def _record_step_history(
        self,
        *,
        sequence: int,
        step_key: str,
        agent_name: str,
        role_desc: str,
        command: str,
        input_data: str,
        output_data: str,
        status: str,
        error_message: str | None,
        timeout_seconds: int,
        max_input_chars: int | None,
        max_output_chars: int | None,
        max_context_chars: int | None,
        is_feedback: bool,
        started_at_utc: str,
        finished_at_utc: str,
        duration_ms: int,
    ) -> None:
        run_id = self._active_run_id
        if run_id is None or self.history_store is None or not self._history_store_available:
            return
        self._safe_history_call(
            operation_label="registrar passo",
            callback=lambda: self.history_store.record_step(
                run_id=run_id,
                sequence=sequence,
                step_key=step_key,
                agent_name=agent_name,
                role_desc=role_desc,
                command=command,
                input_data=input_data,
                output_data=output_data,
                status=status,
                error_message=error_message,
                timeout_seconds=timeout_seconds,
                max_input_chars=max_input_chars,
                max_output_chars=max_output_chars,
                max_context_chars=max_context_chars,
                is_feedback=is_feedback,
                started_at_utc=started_at_utc,
                finished_at_utc=finished_at_utc,
                duration_ms=duration_ms,
            ),
        )

    def _safe_history_call(self, operation_label: str, callback):
        if self.history_store is None or not self._history_store_available:
            return None
        try:
            return callback()
        except Exception as exc:  # pragma: no cover - comportamento defensivo
            self._history_store_available = False
            if not self._history_store_warning_emitted:
                self._history_store_warning_emitted = True
                self.ui.show_error(
                    f"Aviso: persistência estruturada indisponível ao {operation_label}: {exc}"
                )
            return None
