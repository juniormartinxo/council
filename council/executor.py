import subprocess
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
        """
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8"
            )
            
            # Escreve o input data para a ferramenta pelo stdin
            if input_data:
                process.stdin.write(input_data)
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
                self.ui.show_error(f"Falha ao executar '{command}' (Código {returncode}):\n{stderr_content}")
                raise CommandError(f"Erro no comando: {command}")
                
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
