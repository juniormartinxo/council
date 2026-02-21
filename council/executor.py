import subprocess
import shlex
from council.ui import UI

class CommandError(Exception):
    """Exceção levantada quando um subprocesso falha."""
    pass

class Executor:
    """Responsável por orquestrar subprocessos CLI capturando stdin/stdout."""
    def __init__(self, ui: UI):
        self.ui = ui

    def run_cli(self, command: str, input_data: str, timeout: int = 120, on_output=None) -> str:
        """
        Executa um comando CLI via subprocess, injetando dados via stdin.
        Lida com timeouts e invoca de forma assíncrona o callback on_output se estiver disponível.

        Suporte especial:
        - Se o comando contiver o placeholder literal {input},
          o conteúdo de input_data é injetado no próprio comando (com escaping seguro),
          e nada é enviado via stdin.
        """
        try:
            command_to_run, stdin_payload = self._prepare_command(command, input_data)

            process = subprocess.Popen(
                command_to_run,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8"
            )
            
            # Escreve o input data para a ferramenta pelo stdin
            if stdin_payload:
                process.stdin.write(stdin_payload)
                process.stdin.flush()
            process.stdin.close() # Sinaliza FIM DE INPUT para a pipeline não travar

            stdout_lines = []
            
            # Lê o stdout linha a linha em tempo real
            for line in iter(process.stdout.readline, ''):
                stdout_lines.append(line)
                if on_output:
                    on_output(line.rstrip('\n'))

            # Tenta pegar erros se der merda
            stderr_content = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            
            returncode = process.wait(timeout=timeout)
            
            if returncode != 0:
                self.ui.show_error(
                    f"Falha ao executar '{command_to_run}' (Código {returncode}):\n{stderr_content}"
                )
                raise CommandError(f"Erro no comando: {command_to_run}")
                
            return "".join(stdout_lines).strip()
            
        except subprocess.TimeoutExpired:
            process.kill()
            self.ui.show_error(f"O comando '{command}' atingiu o timeout de {timeout}s.")
            raise CommandError(f"Timeout no comando: {command}")
        except Exception as e:
            if not isinstance(e, CommandError):
                self.ui.show_error(f"Erro do sistema ao rodar '{command}': {str(e)}")
                raise CommandError(f"Erro no ambiente: {str(e)}")
            raise

    def _prepare_command(self, command: str, input_data: str) -> tuple[str, str]:
        """
        Resolve o comando final e define se o payload será enviado por stdin.
        """
        if "{input}" not in command:
            if self._is_gemini_prompt_missing_value(command):
                quoted_input = shlex.quote(input_data or "")
                return f"{command} {quoted_input}", ""
            return command, input_data

        quoted_input = shlex.quote(input_data)
        prepared_command = command.replace("{input}", quoted_input)
        return prepared_command, ""

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
