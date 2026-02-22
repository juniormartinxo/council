import subprocess
import shlex
import threading
import os
import signal
import tempfile
from typing import TextIO

from council.limits import read_positive_int_env
from council.ui import UI


DEFAULT_MAX_INPUT_CHARS = 120_000
DEFAULT_MAX_OUTPUT_CHARS = 200_000
MAX_INPUT_CHARS_ENV_VAR = "COUNCIL_MAX_INPUT_CHARS"
MAX_OUTPUT_CHARS_ENV_VAR = "COUNCIL_MAX_OUTPUT_CHARS"
OUTPUT_TRUNCATION_NOTICE = (
    "[... saída truncada para o limite configurado; conteúdo completo descartado para preservar memória ...]\n"
)
CLI_INPUT_BLOCK_START = "===COUNCIL_INPUT_ARGV_START==="
CLI_INPUT_BLOCK_END = "===COUNCIL_INPUT_ARGV_END==="

class CommandError(Exception):
    """Exceção levantada quando um subprocesso falha."""
    pass

class ExecutionAborted(CommandError):
    """Execução interrompida pelo usuário."""
    pass

class Executor:
    """Responsável por orquestrar subprocessos CLI capturando stdin/stdout."""
    def __init__(
        self,
        ui: UI,
        max_input_chars: int | None = None,
        max_output_chars: int | None = None,
    ):
        self.ui = ui
        if max_input_chars is None:
            self.max_input_chars = read_positive_int_env(
                MAX_INPUT_CHARS_ENV_VAR,
                DEFAULT_MAX_INPUT_CHARS,
            )
        else:
            self.max_input_chars = max_input_chars
        if max_output_chars is None:
            self.max_output_chars = read_positive_int_env(
                MAX_OUTPUT_CHARS_ENV_VAR,
                DEFAULT_MAX_OUTPUT_CHARS,
            )
        else:
            self.max_output_chars = max_output_chars
        if self.max_input_chars <= 0:
            raise ValueError("max_input_chars deve ser um inteiro positivo.")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars deve ser um inteiro positivo.")
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._current_process: subprocess.Popen | None = None

    def request_cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            process = self._current_process

        if process is None or process.poll() is not None:
            return

        self._terminate_process(process)

    def run_cli(
        self,
        command: str,
        input_data: str,
        timeout: int = 120,
        on_output=None,
        max_input_chars: int | None = None,
        max_output_chars: int | None = None,
    ) -> str:
        """
        Executa um comando CLI via subprocess, injetando dados via stdin.
        Lida com timeouts e invoca de forma assíncrona o callback on_output se estiver disponível.

        Suporte especial:
        - Se o comando contiver o placeholder literal {input},
          o conteúdo de input_data é injetado como argumento literal no argv final,
          e nada é enviado via stdin.
        """
        process: subprocess.Popen | None = None
        output_spool: TextIO | None = None
        try:
            if self._cancel_event.is_set():
                raise ExecutionAborted("Execução abortada pelo usuário.")

            if timeout <= 0:
                self.ui.show_error("Timeout inválido: informe um inteiro positivo.")
                raise CommandError("Timeout inválido")

            effective_max_input_chars = self.max_input_chars if max_input_chars is None else max_input_chars
            effective_max_output_chars = self.max_output_chars if max_output_chars is None else max_output_chars

            if effective_max_input_chars <= 0:
                self.ui.show_error("max_input_chars inválido: informe um inteiro positivo.")
                raise CommandError("max_input_chars inválido")
            if effective_max_output_chars <= 0:
                self.ui.show_error("max_output_chars inválido: informe um inteiro positivo.")
                raise CommandError("max_output_chars inválido")

            if len(input_data) > effective_max_input_chars:
                self.ui.show_error(
                    (
                        f"Input excedeu o limite configurado de {effective_max_input_chars} caracteres "
                        f"para '{command}'."
                    )
                )
                raise CommandError(f"Input acima do limite para: {command}")

            command_argv, stdin_payload = self._prepare_command(command, input_data)
            command_display = shlex.join(command_argv)

            process = subprocess.Popen(
                command_argv,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                start_new_session=True,
            )

            with self._process_lock:
                self._current_process = process
            
            # Escreve o input data para a ferramenta pelo stdin
            if stdin_payload:
                process.stdin.write(stdin_payload)
                process.stdin.flush()
            process.stdin.close() # Sinaliza FIM DE INPUT para a pipeline não travar

            stdout_lines = []
            captured_stdout_chars = 0
            tail_chunks: list[str] = []
            tail_chars = 0

            def append_tail(chunk: str) -> tuple[int, list[str]]:
                nonlocal tail_chars
                if not chunk:
                    return tail_chars, tail_chunks
                tail_chunks.append(chunk)
                tail_chars += len(chunk)

                while tail_chars > effective_max_output_chars and tail_chunks:
                    overflow = tail_chars - effective_max_output_chars
                    first = tail_chunks[0]
                    if len(first) <= overflow:
                        tail_chars -= len(first)
                        tail_chunks.pop(0)
                        continue
                    tail_chunks[0] = first[overflow:]
                    tail_chars -= overflow

                return tail_chars, tail_chunks
            
            # Lê o stdout linha a linha em tempo real
            for line in iter(process.stdout.readline, ''):
                if self._cancel_event.is_set():
                    self._terminate_process(process)
                    break

                projected_size = captured_stdout_chars + len(line)
                if output_spool is None and projected_size <= effective_max_output_chars:
                    stdout_lines.append(line)
                    captured_stdout_chars = projected_size
                else:
                    if output_spool is None:
                        output_spool = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
                        baseline_output = "".join(stdout_lines)
                        output_spool.write(baseline_output)
                        append_tail(baseline_output)
                        stdout_lines.clear()
                    output_spool.write(line)
                    append_tail(line)

                if on_output:
                    on_output(line.rstrip('\n'))

            # Tenta pegar erros se der merda
            stderr_content = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            
            returncode = process.wait(timeout=timeout)

            if self._cancel_event.is_set():
                raise ExecutionAborted("Execução abortada pelo usuário.")
            
            if returncode != 0:
                self.ui.show_error(
                    f"Falha ao executar '{command_display}' (Código {returncode}):\n{stderr_content}"
                )
                raise CommandError(f"Erro no comando: {command_display}")

            if output_spool is None:
                return "".join(stdout_lines).strip()

            truncated_output = "".join(tail_chunks).strip()
            return f"{OUTPUT_TRUNCATION_NOTICE}{truncated_output}".strip()
            
        except subprocess.TimeoutExpired:
            if process is not None:
                self._terminate_process(process)
            self.ui.show_error(f"O comando '{command}' atingiu o timeout de {timeout}s.")
            raise CommandError(f"Timeout no comando: {command}")
        except ExecutionAborted:
            raise
        except Exception as e:
            if not isinstance(e, CommandError):
                self.ui.show_error(f"Erro do sistema ao rodar '{command}': {str(e)}")
                raise CommandError(f"Erro no ambiente: {str(e)}")
            raise
        finally:
            if output_spool is not None:
                output_spool.close()
            with self._process_lock:
                self._current_process = None

    def _prepare_command(self, command: str, input_data: str) -> tuple[list[str], str]:
        """
        Resolve o comando final e define se o payload será enviado por stdin.
        """
        command_tokens = shlex.split(command)
        argv_payload = self._wrap_argv_input_payload(input_data)

        if "{input}" not in command:
            if self._is_gemini_prompt_missing_value(command):
                return [*command_tokens, argv_payload], ""
            return command_tokens, input_data

        prepared_tokens = [token.replace("{input}", argv_payload) for token in command_tokens]
        return prepared_tokens, ""

    def _is_gemini_prompt_missing_value(self, command: str) -> bool:
        """
        Detecta comandos `gemini -p` / `gemini --prompt` sem valor.
        """
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False

        if not tokens:
            return False

        binary_name = tokens[0].split("/")[-1]
        if binary_name != "gemini":
            return False

        for index, token in enumerate(tokens):
            if token in {"-p", "--prompt"}:
                return index + 1 >= len(tokens)
            if token.startswith("--prompt="):
                return False

        return False

    def _wrap_argv_input_payload(self, payload: str) -> str:
        normalized_payload = payload.strip()
        if not normalized_payload:
            return ""
        return (
            f"{CLI_INPUT_BLOCK_START}\n"
            "PROMPT INTEGRAL ENVIADO VIA ARGV.\n"
            f"{normalized_payload}\n"
            f"{CLI_INPUT_BLOCK_END}"
        )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return

        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                process.terminate()
        else:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    process.kill()
            else:
                process.kill()
