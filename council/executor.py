import subprocess
import shlex
import threading
import os
import signal
import tempfile
import logging
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import perf_counter
from typing import TextIO

from council.audit_log import get_audit_logger, log_event
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
DEEPSEEK_COMMAND_NAME = "deepseek"
DEEPSEEK_API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
DEEPSEEK_API_BASE_URL_ENV_VAR = "DEEPSEEK_API_BASE_URL"
DEFAULT_DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


@dataclass(frozen=True)
class _DeepSeekCommandConfig:
    model: str = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEFAULT_DEEPSEEK_API_BASE_URL
    temperature: float | None = None
    max_tokens: int | None = None

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
        self._audit_logger = get_audit_logger()

    def request_cancel(self) -> None:
        self._cancel_event.set()
        log_event(self._audit_logger, "executor.cancel.requested", level=logging.INFO)
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
        command_display = command
        run_started_perf = perf_counter()
        error_logged = False
        try:
            # Executor instances can be reused; avoid stale cancel state from previous runs.
            self._cancel_event.clear()

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

            command_tokens = shlex.split(command)
            if self._is_deepseek_command_tokens(command_tokens):
                command_display = shlex.join(command_tokens)
                log_event(
                    self._audit_logger,
                    "executor.command.start",
                    level=logging.INFO,
                    command=command_display,
                    timeout_seconds=timeout,
                    input_chars=len(input_data),
                    stdin_chars=0,
                    max_input_chars=effective_max_input_chars,
                    max_output_chars=effective_max_output_chars,
                )
                try:
                    deepseek_output = self._run_deepseek_api(
                        command_tokens=command_tokens,
                        input_data=input_data,
                        timeout=timeout,
                    )
                except ExecutionAborted:
                    raise
                except CommandError as exc:
                    log_event(
                        self._audit_logger,
                        "executor.command.failed",
                        level=logging.ERROR,
                        command=command_display,
                        return_code=None,
                        stderr_chars=0,
                        duration_ms=int((perf_counter() - run_started_perf) * 1000),
                    )
                    error_logged = True
                    self.ui.show_error(f"Falha ao executar '{command_display}':\n{exc}")
                    raise

                final_output, output_truncated = self._truncate_output(
                    deepseek_output,
                    max_chars=effective_max_output_chars,
                )
                if on_output and final_output:
                    for line in final_output.splitlines():
                        on_output(line)
                log_event(
                    self._audit_logger,
                    "executor.command.completed",
                    level=logging.INFO,
                    command=command_display,
                    return_code=0,
                    output_chars=len(final_output),
                    output_truncated=output_truncated,
                    duration_ms=int((perf_counter() - run_started_perf) * 1000),
                )
                return final_output

            command_argv, stdin_payload = self._prepare_command(command, input_data)
            command_display = shlex.join(command_argv)
            log_event(
                self._audit_logger,
                "executor.command.start",
                level=logging.INFO,
                command=command_display,
                timeout_seconds=timeout,
                input_chars=len(input_data),
                stdin_chars=len(stdin_payload),
                max_input_chars=effective_max_input_chars,
                max_output_chars=effective_max_output_chars,
            )

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
                log_event(
                    self._audit_logger,
                    "executor.command.failed",
                    level=logging.ERROR,
                    command=command_display,
                    return_code=returncode,
                    stderr_chars=len(stderr_content),
                    duration_ms=int((perf_counter() - run_started_perf) * 1000),
                )
                error_logged = True
                self.ui.show_error(
                    f"Falha ao executar '{command_display}' (Código {returncode}):\n{stderr_content}"
                )
                raise CommandError(f"Erro no comando: {command_display}")

            if output_spool is None:
                final_output = "".join(stdout_lines).strip()
                log_event(
                    self._audit_logger,
                    "executor.command.completed",
                    level=logging.INFO,
                    command=command_display,
                    return_code=returncode,
                    output_chars=len(final_output),
                    output_truncated=False,
                    duration_ms=int((perf_counter() - run_started_perf) * 1000),
                )
                return final_output

            truncated_output = "".join(tail_chunks).strip()
            final_output = f"{OUTPUT_TRUNCATION_NOTICE}{truncated_output}".strip()
            log_event(
                self._audit_logger,
                "executor.command.completed",
                level=logging.INFO,
                command=command_display,
                return_code=returncode,
                output_chars=len(final_output),
                output_truncated=True,
                duration_ms=int((perf_counter() - run_started_perf) * 1000),
            )
            return final_output
            
        except subprocess.TimeoutExpired:
            if process is not None:
                self._terminate_process(process)
            log_event(
                self._audit_logger,
                "executor.command.timeout",
                level=logging.ERROR,
                command=command_display,
                timeout_seconds=timeout,
                duration_ms=int((perf_counter() - run_started_perf) * 1000),
            )
            error_logged = True
            self.ui.show_error(f"O comando '{command}' atingiu o timeout de {timeout}s.")
            raise CommandError(f"Timeout no comando: {command}")
        except ExecutionAborted:
            log_event(
                self._audit_logger,
                "executor.command.aborted",
                level=logging.INFO,
                command=command_display,
                duration_ms=int((perf_counter() - run_started_perf) * 1000),
            )
            raise
        except Exception as e:
            if not isinstance(e, CommandError):
                log_event(
                    self._audit_logger,
                    "executor.command.error",
                    level=logging.ERROR,
                    command=command_display,
                    error=str(e),
                    error_type=type(e).__name__,
                    duration_ms=int((perf_counter() - run_started_perf) * 1000),
                )
                self.ui.show_error(f"Erro do sistema ao rodar '{command}': {str(e)}")
                raise CommandError(f"Erro no ambiente: {str(e)}")
            if not error_logged:
                log_event(
                    self._audit_logger,
                    "executor.command.error",
                    level=logging.ERROR,
                    command=command_display,
                    error=str(e),
                    error_type=type(e).__name__,
                    duration_ms=int((perf_counter() - run_started_perf) * 1000),
                )
            raise
        finally:
            if output_spool is not None:
                output_spool.close()
            with self._process_lock:
                self._current_process = None

    def _is_deepseek_command_tokens(self, command_tokens: list[str]) -> bool:
        return bool(command_tokens) and command_tokens[0] == DEEPSEEK_COMMAND_NAME

    def _run_deepseek_api(
        self,
        *,
        command_tokens: list[str],
        input_data: str,
        timeout: int,
    ) -> str:
        if self._cancel_event.is_set():
            raise ExecutionAborted("Execução abortada pelo usuário.")

        command_config = self._parse_deepseek_command(command_tokens)
        api_key = os.getenv(DEEPSEEK_API_KEY_ENV_VAR, "").strip()
        if not api_key:
            raise CommandError(
                f"Defina {DEEPSEEK_API_KEY_ENV_VAR} para usar o provider DeepSeek."
            )

        payload: dict[str, object] = {
            "model": command_config.model,
            "messages": [{"role": "user", "content": input_data}],
            "stream": False,
        }
        if command_config.temperature is not None:
            payload["temperature"] = command_config.temperature
        if command_config.max_tokens is not None:
            payload["max_tokens"] = command_config.max_tokens

        endpoint = f"{command_config.base_url}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_response = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            error_message = self._extract_deepseek_error_message(error_body) or str(exc)
            raise CommandError(f"API DeepSeek retornou erro HTTP {exc.code}: {error_message}") from exc
        except urllib.error.URLError as exc:
            raise CommandError(f"Falha de conexão com a API DeepSeek: {exc.reason}") from exc

        if self._cancel_event.is_set():
            raise ExecutionAborted("Execução abortada pelo usuário.")

        return self._extract_deepseek_response_text(raw_response)

    def _parse_deepseek_command(self, command_tokens: list[str]) -> _DeepSeekCommandConfig:
        if not self._is_deepseek_command_tokens(command_tokens):
            raise CommandError("Comando DeepSeek inválido.")

        model = DEFAULT_DEEPSEEK_MODEL
        base_url = os.getenv(DEEPSEEK_API_BASE_URL_ENV_VAR, DEFAULT_DEEPSEEK_API_BASE_URL).strip()
        temperature: float | None = None
        max_tokens: int | None = None

        index = 1
        while index < len(command_tokens):
            token = command_tokens[index]

            if token in {"--model", "-m"}:
                model, index = self._read_required_token_value(command_tokens, index, token)
                continue
            if token.startswith("--model="):
                model = token.partition("=")[2].strip()
                if not model:
                    raise CommandError("Valor de --model não pode ser vazio no comando deepseek.")
                index += 1
                continue
            if token in {"--temperature", "-t"}:
                raw_value, index = self._read_required_token_value(command_tokens, index, token)
                try:
                    temperature = float(raw_value)
                except ValueError as exc:
                    raise CommandError(f"Valor inválido para {token}: {raw_value}") from exc
                continue
            if token.startswith("--temperature="):
                raw_value = token.partition("=")[2].strip()
                try:
                    temperature = float(raw_value)
                except ValueError as exc:
                    raise CommandError(f"Valor inválido para --temperature: {raw_value}") from exc
                index += 1
                continue
            if token == "--max-tokens":
                raw_value, index = self._read_required_token_value(command_tokens, index, token)
                max_tokens = self._parse_positive_int_value(raw_value, token)
                continue
            if token.startswith("--max-tokens="):
                raw_value = token.partition("=")[2].strip()
                max_tokens = self._parse_positive_int_value(raw_value, "--max-tokens")
                index += 1
                continue
            if token == "--base-url":
                base_url, index = self._read_required_token_value(command_tokens, index, token)
                continue
            if token.startswith("--base-url="):
                base_url = token.partition("=")[2].strip()
                if not base_url:
                    raise CommandError("Valor de --base-url não pode ser vazio no comando deepseek.")
                index += 1
                continue

            raise CommandError(
                "Comando deepseek inválido. Opções suportadas: "
                "--model/-m, --temperature/-t, --max-tokens e --base-url."
            )

        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise CommandError("URL base da API DeepSeek não pode ser vazia.")
        if not model.strip():
            raise CommandError("Modelo DeepSeek não pode ser vazio.")

        return _DeepSeekCommandConfig(
            model=model.strip(),
            base_url=normalized_base_url,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _read_required_token_value(
        self,
        command_tokens: list[str],
        current_index: int,
        option_name: str,
    ) -> tuple[str, int]:
        next_index = current_index + 1
        if next_index >= len(command_tokens):
            raise CommandError(f"O comando deepseek exige valor para {option_name}.")
        value = command_tokens[next_index].strip()
        if not value:
            raise CommandError(f"O comando deepseek exige valor não vazio para {option_name}.")
        return value, next_index + 1

    def _parse_positive_int_value(self, raw_value: str, option_name: str) -> int:
        try:
            parsed = int(raw_value)
        except ValueError as exc:
            raise CommandError(f"Valor inválido para {option_name}: {raw_value}") from exc
        if parsed <= 0:
            raise CommandError(f"{option_name} deve ser um inteiro positivo.")
        return parsed

    def _extract_deepseek_error_message(self, raw_body: str) -> str | None:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return raw_body.strip() or None
        if not isinstance(payload, dict):
            return raw_body.strip() or None
        if "error" not in payload:
            return None
        error_value = payload.get("error")
        if isinstance(error_value, dict):
            message = error_value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error_value, str) and error_value.strip():
            return error_value.strip()
        return None

    def _extract_deepseek_response_text(self, raw_response: str) -> str:
        try:
            payload = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise CommandError("Resposta inválida da API DeepSeek (JSON malformado).") from exc

        if not isinstance(payload, dict):
            raise CommandError("Resposta inválida da API DeepSeek (objeto JSON esperado).")

        error_message = self._extract_deepseek_error_message(raw_response)
        if error_message and "choices" not in payload:
            raise CommandError(f"Erro retornado pela API DeepSeek: {error_message}")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CommandError("Resposta da API DeepSeek sem campo 'choices'.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise CommandError("Resposta da API DeepSeek com formato de choice inválido.")

        message_payload = first_choice.get("message")
        if not isinstance(message_payload, dict):
            raise CommandError("Resposta da API DeepSeek sem campo 'message'.")

        content = self._extract_text_content(message_payload.get("content"))
        reasoning = self._extract_text_content(message_payload.get("reasoning_content"))
        text = content or reasoning
        if not text:
            raise CommandError("Resposta da API DeepSeek sem conteúdo de texto.")
        return text.strip()

    def _extract_text_content(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""

        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    def _truncate_output(self, output: str, *, max_chars: int) -> tuple[str, bool]:
        cleaned_output = output.strip()
        if len(cleaned_output) <= max_chars:
            return cleaned_output, False
        truncated = cleaned_output[-max_chars:].strip()
        return f"{OUTPUT_TRUNCATION_NOTICE}{truncated}".strip(), True

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
