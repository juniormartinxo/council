# Application Flow (Council)

Este documento descreve o fluxo ponta-a-ponta da execução do Council, desde o comando inicial até a finalização de um run.

## 1. Visão Geral

O fluxo cobre:

- bootstrap via CLI/TUI;
- resolução e validação de `flow.json`;
- orquestração passo a passo;
- montagem de prompt com blocos de dados delimitados;
- execução em subprocesso (`stdin` ou `argv`);
- streaming, histórico e encerramento.

## 2. Diagrama Mermaid

```mermaid
flowchart TD
    A["Usuario executa council run ou council tui"] --> B["main.py parseia argumentos"]
    B --> C["Resolve flow config: cli, env, cwd, user ou default"]
    C --> D["Carrega e valida FlowStep"]
    D --> E["Inicializa CouncilState, UI, Executor e Orchestrator"]
    E --> F["orchestrator.run_flow(user_prompt)"]
    F --> G["state.add_turn(Human), painel inicial e abertura de run no history"]
    G --> H{"Para cada passo"}

    H --> I["Monta template context com full_context, last_output e outputs anteriores encapsulados"]
    I --> J["render_step_input(step, context)"]
    J --> K["_step"]
    K --> L["executor.run_cli(command, input_data)"]

    L --> M{"command contem placeholder input?"}
    M -- "Sim" --> N["Encapsula payload e injeta no argv"]
    M -- "Nao" --> O{"gemini prompt sem valor?"}
    O -- "Sim" --> P["Encapsula payload e anexa ao argv"]
    O -- "Nao" --> Q["Envia input_data via stdin"]

    N --> R["subprocess.Popen com shell false"]
    P --> R
    Q --> R

    R --> S["Streaming de stdout para UI + coleta de output"]
    S --> T{"Erro / timeout / abort?"}
    T -- "Sim" --> U["Registra erro, fecha run, encerra fluxo"]
    T -- "Nao" --> V["state.add_turn assistant, show_panel e grava step no history"]

    V --> W{"UI pediu feedback humano?"}
    W -- "Sim" --> X["Monta follow-up com resposta anterior encapsulada"]
    X --> K
    W -- "Nao" --> Y["Atualiza step_outputs e last_output"]
    Y --> H

    H --> Z["Fim dos passos: fecha run como sucesso"]
    Z --> AA["Exibe sucesso final"]
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
