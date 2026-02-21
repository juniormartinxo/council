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
A orquestração do pipeline é iniciada via binário `council`, demandando o prompt primário e passando-o para ser triturado na topologia de múltiplos agentes:

```bash
# Formato padrão
council run "<Seu_Prompt_Arquitetural>"

# Formato com fluxo customizado (papéis/agentes definidos pelo dev)
council run "<Seu_Prompt_Arquitetural>" --flow-config flow.example.json

# Modo TUI (Textual)
council tui

# Modo TUI já com prompt/flow preenchidos
council tui -p "<Seu_Prompt_Arquitetural>" -c flow.example.json

# Exemplos Operacionais
council run "Crie um script robusto de backup de sistema"
council run "Prototipe a modelagem de dados para uma rede blockchain simples"
```

Instalação global recomendada para rodar em qualquer diretório:

```bash
pipx install .

# Depois de instalado:
council run "<Seu_Prompt_Arquitetural>"
council tui
```

No modo TUI, os comportamentos atuais são:

- **Execução do fluxo:** por botão `Executar`, tecla `Enter` no campo de prompt ou `Ctrl+R`.
- **Checkpoint humano por etapa:** após cada agente, escolha `Continuar`, `Enviar ajuste` (reexecuta o mesmo step com feedback) ou `Abortar`.
- **Abas por step em dois painéis:** tanto em `Stream em tempo real` quanto em `Resultados por etapa`, com aba agregada `Geral`.
- **Cópia contextual por aba ativa:** `Ctrl+1` copia o conteúdo da aba ativa no painel de stream; `Ctrl+2` copia a aba ativa do painel de resultados.
- **Fallback de clipboard:** se o terminal não permitir copiar para o clipboard do SO, a TUI salva em `/tmp/council_<stream|resultados>_<timestamp>.txt` e informa o caminho.
- **Persistência local de sessão:** o arquivo `tui_state.json` em `~/.config/council/` (ou equivalente do SO) guarda o `flow_config` usado por último e o histórico de prompts.
- **Histórico de prompt com navegação:** use setas `↑/↓` no input para recuperar prompts anteriores.
- **Comportamento de abertura:** campo de prompt inicia vazio; campo de flow reabre com o último valor salvo.
- **Abortar a qualquer momento:** use botão `Abortar` ou `Ctrl+X`; o subprocesso em execução é cancelado de forma ativa.
- **Fechar a aplicação:** `Ctrl+Q`.

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
  - Validacao de seguranca no parse: o binario deve existir no `PATH`; `\n`, `\r`, `|`, `&&`, `;`, `` ` ``, `$(`, `>`, `>>` sao rejeitados.
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

**Resolução automática de fluxo (quando `--flow-config` não é informado):**
1. variável de ambiente `COUNCIL_FLOW_CONFIG`;
2. `./flow.json` no diretório atual;
3. `~/.config/council/flow.json` (ou equivalente no SO);
4. fluxo default interno.

Você pode sobrescrever o diretório de configuração do usuário com `COUNCIL_HOME`.

## 5. Práticas de Isolamento e Segurança de CLI
CLIs modernos muitas vezes atuam como executáveis que lêem e modificam arquivos locais onde o kernel/TUI está operando. O Council injeta _Flags_ para forçar os modos interativos em _Modo Impressão em stdout_ ("Print Mode") anulando capacidades autônomas acidentais, mantendo as execuções isoladas a "Cálculos LLM puros" durante o repasse em lote do _Subprocess Communicator_.
