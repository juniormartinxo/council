# Operações e Manutenção Diária do Council (MAS)

Este documento contém o manual tático para execução local do repositório, mitigação de bugs em ambientes isolados e orientações de desenvolvimento e monitoramento do Council.

## 1. Topologia de Instalação e Requisitos Básicos

O Council demanda um isolamento em **Virtual Environment** (venv/virtualenv), prevenindo choques de versões (Dependency Hell) e bloqueios por ambientes externos gerenciados nativamente por seu Sistema Operacional:

```bash
# Passos canônicos de bootstrap de desenvolvimento
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Opcional com `direnv` para autoativar a `.venv` ao entrar no diretório:

```bash
direnv allow
# opcional: cp .envrc.local.example .envrc.local
```

> **Aviso Operacional:** O fluxo pode combinar providers via CLI (`claude`, `gemini`, `codex`, `ollama`) e via API (`deepseek`). CLIs exigem binário no `PATH`; para DeepSeek, configure `DEEPSEEK_API_KEY`.

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
- **Fallback de clipboard:** se o terminal não permitir copiar para o clipboard do SO, a TUI salva em `COUNCIL_HOME/clipboard/council_<stream|resultados>_<timestamp>.txt` e informa o caminho.
- **Persistência local de sessão:** o arquivo `tui_state.json` em `COUNCIL_HOME` guarda `last_flow_config` e histórico de prompts.
- **Histórico de prompt com navegação:** use setas `↑/↓` no input para recuperar prompts anteriores.
- **Comportamento de abertura:** campo de prompt inicia vazio; campo de flow reabre com o último valor salvo.
- **Abortar a qualquer momento:** use botão `Abortar` ou `Ctrl+X`; o subprocesso em execução é cancelado de forma ativa.
- **Fechar a aplicação:** `Ctrl+Q`.

Limpeza explícita do histórico sensível:

```bash
council history clear
```

Inspeção rápida de runs persistidos no SQLite local:

```bash
council history runs --limit 20
```

Diagnóstico de pré-requisitos de binários para o fluxo:

```bash
council doctor

# Validando um fluxo específico
council doctor --flow-config flow.example.json
```

Assinatura de `flow.json` (integridade/autoria):

```bash
# requer pacote opcional: pip install -e ".[security]"
council flow keygen --key-id equipe-seguranca-v1 --trust
council flow sign flow.example.json --private-key equipe-seguranca-v1.key.pem --key-id equipe-seguranca-v1
council flow verify flow.example.json

# modo estrito: bloqueia execução sem assinatura válida
export COUNCIL_REQUIRE_FLOW_SIGNATURE=1
```

Criptografia at-rest opcional do histórico de prompts:

```bash
pip install -e ".[security]"
export COUNCIL_TUI_STATE_PASSPHRASE="sua-senha-forte"
```

Alternativa para reduzir exposição da senha em variáveis de ambiente:

```bash
printf '%s' 'sua-senha-forte' > ~/.config/council/passphrase.txt
chmod 600 ~/.config/council/passphrase.txt
export COUNCIL_TUI_STATE_PASSPHRASE_FILE=~/.config/council/passphrase.txt
```

Persistência estruturada do pipeline:
- Banco em `COUNCIL_HOME/db/history.sqlite3`.
- Tabelas principais: `runs` (metadados da execução) e `run_steps` (passos executados com input/output e duração).

Logging de auditoria:
- Arquivo em `COUNCIL_HOME/council.log` com eventos estruturados (JSON por linha).
- Campos padrão: `timestamp_utc`, `level`, `event`, `data`.
- Nível mínimo configurável via `COUNCIL_LOG_LEVEL` (default: `INFO`; aceita `WARNING` e `WARN`). Valores inválidos falham explicitamente (fail-fast).
- Rotação local por tamanho configurável:
  - `COUNCIL_LOG_MAX_BYTES` (default: `5242880`, 5 MiB por arquivo)
  - `COUNCIL_LOG_BACKUP_COUNT` (default: `5`, quantidade de arquivos `.1`, `.2`, ...)
- Valores inválidos de rotação (`<= 0` ou não numéricos) também falham explicitamente na inicialização.
- Permissões endurecidas: `COUNCIL_HOME` em `0o700` e `council.log` em `0o600` quando suportado.
- Eventos auditados incluem execução de `run`, `tui` e `doctor` (incluindo warnings de pré-requisito e resultado final).

Inspeção rápida dos últimos eventos:

```bash
tail -n 50 "${COUNCIL_HOME:-$HOME/.config/council}/council.log"
```

## 3. Comandos Externos Subjacentes vs Diagnóstico
Em caso de falha nas integrações LLM (CLI ou API), os erros são interceptados e exibidos na UI do Council.
- No caminho CLI, a falha normalmente vem de `stderr` + `exit code` não-zero.
- No caminho API (DeepSeek), a falha vem de erro HTTP/rede com mensagem normalizada pelo executor.

O `council run` e a TUI validam automaticamente os pré-requisitos exigidos pelo fluxo antes da execução.
Para diagnóstico explícito (binários resolvidos e providers API), use `council doctor`.

Para debugar a anomalia fora da esteira:
- `claude -p "teste debug"`
- `gemini -p "teste debug"`
- `codex exec --skip-git-repo-check "teste debug"`
- para `deepseek`, valide o token: `echo "$DEEPSEEK_API_KEY"` (não vazio) e rode um fluxo com `command: "deepseek --model deepseek-chat"`.

## 4. Evolução do Código e Configurações (Extensibilidade)
Esta estrutura Modular e Polimórfica foi concebida para que futuros _DevOps_ ou Engenheiros amplifiquem e expandam os Modelos Interagentes sem quebrar o loop central:

A modificação de novos sub-agentes não requer mexer na interface de usuário nem em `orchestrator.py`. O fluxo agora é dirigido por configuração JSON (`--flow-config` ou env var `COUNCIL_FLOW_CONFIG`), permitindo definir qual IA executa cada papel e em qual ordem.

Cada passo aceita, entre outros campos:
- `key`: identificador do resultado para ser reutilizado em passos seguintes.
- `agent_name` / `role_desc`: rótulos exibidos na UI.
- `command`: CLI/provedor real que será executado (ex: `claude -p`, `gemini -p {input}`, `codex exec --skip-git-repo-check`, `deepseek --model deepseek-chat`).
  - Validacao de seguranca no parse: o primeiro token deve estar na allowlist (`claude`, `gemini`, `codex`, `ollama`, `deepseek`) e nao pode usar caminho explicito; `\n`, `\r`, `|`, `&&`, `;`, `` ` ``, `$(`, `>`, `>>` sao rejeitados.
  - Para CLIs, o binario deve existir no `PATH`. `deepseek` e API-only e nao exige binario local.
- `instruction`: instrução principal do papel.
- `input_template`: template com variáveis (`{user_prompt}`, `{full_context}`, `{last_output}` e `{key}` de passos anteriores).
- `enabled`: quando `false`, o passo é mantido no fluxo, mas é pulado na execução.

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
