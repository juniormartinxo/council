# Application Flow (Council)

Este documento descreve o fluxo ponta-a-ponta da execução do Council, desde o comando inicial até a finalização de um run.

## 1. Visão Geral

O fluxo cobre:

- bootstrap via CLI/TUI;
- resolução e validação de `flow.json`;
- inicialização do logger de auditoria e validação de env vars de logging;
- orquestração passo a passo;
- montagem de prompt com blocos de dados delimitados;
- execução em subprocesso (`stdin` ou `argv`);
- streaming, histórico, auditoria e encerramento.

## 2. Diagrama Mermaid

```mermaid
flowchart TD
    A["Usuario executa council run ou council tui"] --> B["main.py parseia argumentos"]
    B --> C["Resolve flow config: cli, env, cwd, user ou default"]
    C --> D["Carrega e valida FlowStep"]
    D --> E["Inicializa logger de auditoria (fail-fast em env vars invalidas)"]
    E --> F["Inicializa CouncilState, UI, Executor e Orchestrator"]
    F --> G["orchestrator.run_flow(user_prompt)"]
    G --> H["state.add_turn(Human), painel inicial e abertura de run no history"]
    H --> I{"Para cada passo"}

    I --> J["Monta template context com full_context, last_output e outputs anteriores encapsulados"]
    J --> K["render_step_input(step, context)"]
    K --> L["_step"]
    L --> M["executor.run_cli(command, input_data)"]

    M --> N{"command contem placeholder input?"}
    N -- "Sim" --> O["Encapsula payload e injeta no argv"]
    N -- "Nao" --> P{"gemini prompt sem valor?"}
    P -- "Sim" --> Q["Encapsula payload e anexa ao argv"]
    P -- "Nao" --> R["Envia input_data via stdin"]

    O --> S["subprocess.Popen com shell false"]
    Q --> S
    R --> S

    S --> T["Streaming de stdout para UI + coleta de output + eventos de auditoria"]
    T --> U{"Erro / timeout / abort?"}
    U -- "Sim" --> V["Registra erro, fecha run, encerra fluxo e audita status final"]
    U -- "Nao" --> W["state.add_turn assistant, show_panel, grava step no history e audita"]

    W --> X{"UI pediu feedback humano?"}
    X -- "Sim" --> Y["Monta follow-up com resposta anterior encapsulada"]
    Y --> L
    X -- "Nao" --> Z["Atualiza step_outputs e last_output"]
    Z --> I

    I --> AA["Fim dos passos: fecha run como sucesso e audita conclusao"]
    AA --> AB["Exibe sucesso final"]
```

## 3. Caminhos de Input no Executor

`Executor._prepare_command()` decide entre dois canais:

1. `stdin`:
- usado quando o comando não contém `{input}` e não é o fallback `gemini -p` sem valor;
- `input_data` é enviado por `stdin`, seguido de `EOF`.

2. `argv`:
- usado quando o comando contém `{input}` ou no fallback de `gemini -p`/`--prompt` sem valor;
- o payload é delimitado com:
  - `===COUNCIL_INPUT_ARGV_START===`
  - `===COUNCIL_INPUT_ARGV_END===`

## 4. Delimitação de Dados Entre Agentes

No `Orchestrator`, os campos abaixo são encapsulados antes do `render_step_input()`:

- `{full_context}`
- `{last_output}`
- `{<key_de_passo_anterior>}`

Blocos usados:

- `===DADOS_DO_AGENTE_ANTERIOR===`
- `===FIM_DADOS_DO_AGENTE_ANTERIOR===`

Objetivo: reduzir prompt injection indireta entre etapas, separando instrução do passo de dados produzidos por agentes anteriores.

## 5. Arquivos-Chave

- `council/main.py`: entrada CLI/TUI e wiring principal.
- `council/config.py`: resolução/validação de `flow_config` e render de templates.
- `council/orchestrator.py`: laço de execução e controle do pipeline.
- `council/executor.py`: subprocessos, timeout, limites e streaming.
- `council/state.py`: histórico em memória e `full_context`.
- `council/history_store.py`: persistência de runs/steps.
- `council/audit_log.py`: logger estruturado, rotação local e hardening de permissões.
