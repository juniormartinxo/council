# Arquitetura do Council (MAS)

Este documento detalha as decisões de arquitetura e design patterns empregados no desenvolvimento do **Council**, um orquestrador CLI assíncrono para agentes fundacionais. O objetivo é explicitar o raciocínio por trás do código-fonte para engenheiros de software, pares e futuros mantenedores.

## 1. Topologia Macro e Separação de Preocupações (SoC)

A base de código do Council adere estritamente à ideia de _Single Responsibility Principle_ (SRP). Não utilizamos frameworks que aglutinem funcionalidades (como LangChain), construindo um ecossistema desacoplado que se sustenta mesmo se substituirmos completamente a camada da CLI ou a camada dos provedores LLM.

- `main.py` -> **Entrypoint / Roteador** (Apenas roteia os argumentos).
- `config.py` -> **Camada de Configuração de Fluxo** (Carrega e valida passos do pipeline em JSON).
- `orchestrator.py` -> **Regra de Negócios / Controlador** (Contém a sequência de chamadas e lógicas de fluxo).
- `executor.py` -> **Camada de Infraestrutura / Adapter** (Acesso direto a chamadas do SO via Subprocess).
- `ui.py` -> **Camada de Apresentação / View** (Isolamento total dos prints baseados no Rich).
- `tui.py` -> **Camada de Apresentação Interativa** (Textual App + adaptador para reaproveitar Orchestrator/Executor).
- `state.py` -> **Repositório em Memória / Entidade** (Armazena a evolução do contexto do agente).
- `history_store.py` -> **Persistência Estruturada Local** (SQLite de runs e steps).
- `audit_log.py` -> **Auditoria Operacional** (logger estruturado com hardening de permissões, fail-fast de configuração e rotação local).
- `limits.py` -> **Política de Limites por Ambiente** (leitura validada de variáveis de limite).

O acoplamento é injetado (via Dependency Injection) na porta de entrada da aplicação, garantindo que o módulo abstrator de orquestração não dependa de instâncias auto-criadas de Infra ou Visualização.

```python
ui = UI()
state = CouncilState()
executor = Executor(ui)
flow_steps = load_flow_steps(flow_config)
orchestrator = Orchestrator(state, executor, ui, flow_steps=flow_steps)
```

## 2. Padrões de I/O em Tempo Real (Non-blocking Console Streaming)

Para prover feedback imediato de um gerador generativo (LLM) _sem custo extra_ ou travas (deadlocks de stdout). O `executor.py` evita `process.communicate(...)` (que seria bloqueante até finalizar a tarefa em memória) ao aplicar:

1. Modificação do File Descriptor para `readline` direto de SO: Lendo dinamicamente e enfileirando bytes assim que a CLI destino os cospem nativamente. Desta forma, eliminamos falsas impressões de "O programa travou" para loops que durariam 3 minutos.
2. Invocação de um _Callback Delegate_ assíncrono repassado via Evento para preenchimento da UI.
3. Despejo coercitivo de buffer e fechamento preemptivo (`stdin.close()`) forçando `EOF`. Sem esse artifício explícito, os binários das ferramentas externas esperariam de forma cíclica (aguardando mais teclado do usuário final), o que ocasionaria zumbis submersos no PTY.

## 3. Gestão de Estado Efêmera Baseada em Eventos

Modelos fundacionais via CLI não são desenhados inerentemente com abstrações de `messages[]` e APIs de histórico. Para compensar, foi construído um repositório isolado de contexto (`CouncilState`):
- Aborda uma `dataclass Turn` que representa o DTO elementar da linha do tempo da arquitetura, gravando emissor, papéis e descrições pragmáticas da ação gerada. Em caso de intervenções interativas da TUI para refinamento, o feedback humano do Dev consolida seu próprio *Turn* rotulado explicitamente através de ações mapeadas no Histórico, injetando correções no meio da esteira invisivelmente para que a IA reprocessadora a absorva.
- Essa matriz sintética se transforma num "payload de strings monolíticas" que antecede todas as próximas execuções via CLI, preservando o contexto arquitetural de um nó anterior para que um novo nó avaliador não necessite recalcular do zero ou desconheça o cenário primário do User.

## 4. Pipeline Dinâmico via Configuração Externa

O fluxo não é mais hardcoded em etapas fixas. A orquestração é orientada por uma lista de passos (`FlowStep`) carregada de:
- fluxo default interno; ou
- arquivo JSON informado via `--flow-config` (ou `COUNCIL_FLOW_CONFIG`).

Cada passo define:
- `agent_name` / `role_desc` / `command` para o roteamento operacional;
- `instruction` e `input_template` para montagem do prompt;
- `key` para expor a saída em passos posteriores.

O `input_template` suporta placeholders como `{user_prompt}`, `{full_context}`, `{last_output}` e qualquer `key` já produzido anteriormente (`{plan}`, `{code}`, etc.), permitindo que o dev decida qual IA assume cada papel sem alterar o core.

No carregamento de `flow.json`, o `config.py` também aplica validação semântica do `command` antes da execução do passo: sintaxe shell válida, primeiro token na allowlist (`claude`, `gemini`, `codex`, `ollama`, `deepseek`), bloqueio de caminho explícito no primeiro token e rejeição de operadores perigosos/quebras de linha. Para comandos CLI o binário precisa existir no `PATH`; `deepseek` é tratado como provider API-only.

Para formato, exemplos e validações operacionais, consulte `FLOW_CONFIG.md`.

## 5. Integração Headless & TUI-Bypassing

Ao injetarmos requisições pipeadas `string -> subprocess(cmd)`, sistemas avançados de TUI (Interfaces baseadas em Terminal ANSI) quebram violando o padrão TTY pseudo-terminal do kernel, resultando no famigerado: `Error: stdin is not a terminal`.

Para garantir orquestração fluida em rotinas invisíveis, o orquestrador impõe explicitamente bandeiras mitigadoras (Headless Mode):
- Codex: Invoca-se através de `codex exec --skip-git-repo-check` para desviar da interface TUI/Menu e anular a validação forçada sobre o repositório Git subjacente.
- Claude: Adicionado o modo _print_ `-p` estritamente para não invocar prompt de aprovação interativo.
- Gemini: no caso `gemini -p`/`--prompt` sem valor explícito, o `executor.py` detecta o padrão e injeta o payload via `argv` em bloco delimitado (`===COUNCIL_INPUT_ARGV_START===`/`===COUNCIL_INPUT_ARGV_END===`), mantendo execução com `subprocess.Popen(..., shell=False)`.

## 6. Auditoria Estruturada e Fail-Fast de Configuração

O Council adota auditoria estruturada como preocupação transversal do runtime:
- eventos de `run`, `tui`, `doctor`, execução de comandos e passos de orquestração são emitidos em `COUNCIL_HOME/council.log`;
- o logger usa JSON por linha com `timestamp_utc`, `level`, `event` e `data`;
- `COUNCIL_LOG_LEVEL`, `COUNCIL_LOG_MAX_BYTES` e `COUNCIL_LOG_BACKUP_COUNT` usam validação explícita e falham na inicialização quando inválidos;
- rotação local por tamanho evita crescimento indefinido do arquivo de log em execuções intensivas.
