# Operações e Manutenção Diária do Council (MAS)

Este documento contém o manual tático para execução local do repositório, mitigação de bugs em ambientes isolados e orientações de desenvolvimento e monitoramento do Council.

## 1. Topologia de Instalação e Requisitos Básicos

O Council demanda um isolamento em **Virtual Environment** (venv/virtualenv), prevenindo choques de versões (Dependency Hell) e bloqueios por ambientes externos gerenciados nativamente por seu Sistema Operacional:

```bash
# Passos canônicos de bootstrap de desenvolvimento
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> **Aviso Operacional:** Os binários externos essenciais que representam os atores subjacentes à arquitetura (`claude`, `gemini`, `codex`) precisam estar globais ($PATH do OS) ou disponíveis na sessão corrente, sendo invocáveis independentemente do Python.

## 2. Invocação Principal
A orquestração do pipeline é iniciada através da invocação do pacote via CLI do Typer e Python, demandando o prompt primário e passando-o para ser triturado na topologia de múltiplos agentes:

```bash
# Formato padrão
python -m council.main run "<Seu_Prompt_Arquitetural>"

# Formato com fluxo customizado (papéis/agentes definidos pelo dev)
python -m council.main run "<Seu_Prompt_Arquitetural>" --flow-config flow.example.json

# Modo TUI (Textual)
python -m council.main tui

# Modo TUI já com prompt/flow preenchidos
python -m council.main tui -p "<Seu_Prompt_Arquitetural>" -c flow.example.json

# Exemplos Operacionais
python -m council.main run "Crie um script robusto de backup de sistema"
python -m council.main run "Prototipe a modelagem de dados para uma rede blockchain simples"
```

No modo TUI, o fluxo agora roda com **checkpoint humano por etapa**:
- após cada agente responder, você escolhe `Continuar`, `Enviar ajuste` (reexecuta o mesmo agente) ou `Abortar`.

## 3. Comandos Externos Subjacentes vs Diagnóstico
Em caso de falha de conexão nas interfaces LLM de retaguarda isoladas do seu projeto (por ausência de internet ou limitação de taxa), os erros serão propagados via _stderr_ sendo interceptados e expostos visualmente na UI de orquestração do Council pelo _Status Exit Code_ não-zero da Thread filho correspondente.

Para debugar a anomalia fora da esteira:
- `claude -p "teste debug"`
- `gemini -p "teste debug"`
- `codex exec --skip-git-repo-check "teste debug"`

## 4. Evolução do Código e Configurações (Extensibilidade)
Esta estrutura Modular e Polimórfica foi concebida para que futuros _DevOps_ ou Engenheiros amplifiquem e expandam os Modelos Interagentes sem quebrar o loop central:

A modificação de novos sub-agentes não requer mexer na interface de usuário nem em `orchestrator.py`. O fluxo agora é dirigido por configuração JSON (`--flow-config` ou env var `COUNCIL_FLOW_CONFIG`), permitindo definir qual IA executa cada papel e em qual ordem.

Cada passo aceita, entre outros campos:
- `key`: identificador do resultado para ser reutilizado em passos seguintes.
- `agent_name` / `role_desc`: rótulos exibidos na UI.
- `command`: CLI real que será executada (ex: `claude -p`, `gemini -p {input}`, `codex exec --skip-git-repo-check`).
- `instruction`: instrução principal do papel.
- `input_template`: template com variáveis (`{user_prompt}`, `{full_context}`, `{last_output}` e `{key}` de passos anteriores).

*Exemplo reduzido de step:*
```json
{
  "key": "security_review",
  "agent_name": "Gemini",
  "role_desc": "Auditoria de Segurança",
  "command": "gemini -p {input}",
  "instruction": "Revise o código com foco em segurança.",
  "input_template": "{instruction}\n\nCódigo:\n{code}",
  "style": "dodger_blue1"
}
```

Guia completo da feature: `FLOW_CONFIG.md`.

## 5. Práticas de Isolamento e Segurança de CLI
CLIs modernos muitas vezes atuam como executáveis que lêem e modificam arquivos locais onde o kernel/TUI está operando. O Council injeta _Flags_ para forçar os modos interativos em _Modo Impressão em stdout_ ("Print Mode") anulando capacidades autônomas acidentais, mantendo as execuções isoladas a "Cálculos LLM puros" durante o repasse em lote do _Subprocess Communicator_.
