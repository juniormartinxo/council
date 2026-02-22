# Configuração de Fluxo de Agentes

Este guia documenta a feature de fluxo configurável do Council, que permite definir qual IA executa cada papel sem alterar código Python.

## 1. Visão Geral

Você pode sobrescrever o pipeline padrão de agentes de duas formas:

1. `--flow-config <arquivo.json>` na execução.
2. Variável de ambiente `COUNCIL_FLOW_CONFIG`.

Se nenhuma configuração for fornecida, o Council usa o fluxo default interno.

Quando `--flow-config` não é informado, a ordem de busca automática é:
1. `COUNCIL_FLOW_CONFIG`;
2. `./flow.json`;
3. `~/.config/council/flow.json` (ou equivalente no SO);
4. fluxo interno default.

Para mudar o diretório base do usuário (incluindo o `flow.json` global), defina `COUNCIL_HOME`.

## 2. Como Configurar

1. Copie o exemplo:

```bash
cp flow.example.json flow.meu.json
```

2. Edite `flow.meu.json` com os agentes/papéis desejados.

3. Execute:

```bash
council run "Seu prompt" --flow-config flow.meu.json
```

Ou, se instalado globalmente:

```bash
council run "Seu prompt" --flow-config flow.meu.json
```

Alternativa via variável de ambiente:

```bash
export COUNCIL_FLOW_CONFIG=flow.meu.json
council run "Seu prompt"
```

### 2.1 Interfaces de Edição (`flow edit`)

O Council possui duas opções de editor no terminal para criação e manutenção do `flow.json`:

1. `tui` (Textual): editor visual.
2. `simple`: editor por prompts no terminal (sem TUI).

Para abrir o editor:

```bash
# Inicia edição num arquivo existente (ou cria um novo se não existir),
# perguntando qual editor usar (tui/simple) em modo interativo.
council flow edit flow.meu.json

# Força editor visual (Textual)
council flow edit flow.meu.json --editor tui

# Força editor simples (prompt no terminal)
council flow edit flow.meu.json --editor simple

# Se não passar caminho, inicia com template padrão.
# Ao salvar, o comando pede o destino do arquivo.
council flow edit
```

No editor TUI, você pode:
- Adicionar ou remover passos visualmente.
- Reordenar etapas do fluxo (Up/Down).
- Ter validação inline do comando e campos críticos antes de salvar.

No editor `simple`, você pode:
- Listar passos atuais.
- Editar, adicionar, remover e mover passos por índice.
- Escolher `role_desc` por opções sugeridas (numéricas) ou texto livre.
- Salvar no final, direto pelo terminal.

> Quando você salva um fluxo por qualquer editor, se houver um arquivo de assinatura `.sig` correspondente, **ele será deletado automaticamente**, visto que a edição invalida a segurança criptográfica anterior. Você precisará assinar o arquivo novamente.
 
## 2.2 Assinatura de Integridade e Autoria (DEF-04)

Para proteger `flow.json` contra adulteração e validar autoria, o Council suporta assinatura Ed25519 com arquivo sidecar `flow.json.sig`.

> Requer dependência opcional de segurança: `pip install -e ".[security]"`.

Fluxo recomendado:

```bash
# 1) gerar chaves
council flow keygen --key-id equipe-seguranca-v1 --trust

# 2) assinar o flow
council flow sign flow.meu.json --private-key equipe-seguranca-v1.key.pem --key-id equipe-seguranca-v1

# 3) verificar manualmente (opcional)
council flow verify flow.meu.json
```

Formato do sidecar (`flow.meu.json.sig`):

```json
{
  "version": 1,
  "algorithm": "ed25519",
  "key_id": "equipe-seguranca-v1",
  "signature": "<base64>"
}
```

O trust store local de chaves públicas fica em:
- `COUNCIL_HOME/trusted_flow_keys/<key_id>.pem`
- Opcionalmente, você pode sobrescrever o diretório com `COUNCIL_TRUSTED_FLOW_KEYS_DIR`.

Para bloquear execução de fluxo sem assinatura válida, habilite modo estrito:

```bash
export COUNCIL_REQUIRE_FLOW_SIGNATURE=1
```

Valores aceitos: `1`, `0`, `true`, `false`, `yes`, `no`, `on`, `off`.

## 3. Estrutura do JSON

O arquivo pode ser:

1. Um objeto com a chave `steps`.
2. Uma lista direta de passos.

Exemplo com `steps`:

```json
{
  "steps": [
    {
      "key": "plan",
      "agent_name": "Claude",
      "role_desc": "Planejamento",
      "command": "claude -p",
      "instruction": "Gere um plano técnico.",
      "input_template": "{instruction}\n\nRequisito:\n{user_prompt}",
      "style": "dark_goldenrod"
    }
  ]
}
```

## 4. Campos de Cada Passo

- `key` (opcional): identificador da saída do passo. Se omitido, vira `step_N`.
- `agent_name` (obrigatório): nome exibido na UI.
- `role_desc` (obrigatório): descrição do papel exibida na UI.
- `command` (obrigatório): comando CLI da IA/ferramenta.
  - Segurança: o primeiro token precisa existir no `PATH`, estar na allowlist (`claude`, `gemini`, `codex`, `ollama`) e não pode usar caminho explícito de binário (ex.: `/usr/bin/codex`); quebras de linha (`\n`, `\r`) e operadores de shell (`|`, `&&`, `;`, `` ` ``, `$(`, `>`, `>>`) são bloqueados.
- `instruction` (obrigatório): instrução principal do passo.
- `input_template` (opcional): template do prompt enviado ao comando. O padrão (default) é `{instruction}\n\n{full_context}`.
- `style` (opcional): cor do painel Rich.
- `is_code` (opcional, boolean): trata saída como código para renderização.
- `timeout` (opcional, inteiro > 0): timeout do passo em segundos. Padrão: `120`.
- `max_input_chars` (opcional, inteiro > 0): limite de input para o passo.
- `max_output_chars` (opcional, inteiro > 0): limite de output mantido em memória/contexto para o passo.
- `max_context_chars` (opcional, inteiro > 0): limite de contexto aplicado somente ao passo.

Alias suportados:

- `key` também pode ser `id`.
- `agent_name` também pode ser `agent`.
- `role_desc` também pode ser `role`.

## 5. Placeholders no `input_template`

Você pode usar:

- `{user_prompt}`: prompt original do usuário.
- `{full_context}`: histórico completo acumulado (encapsulado automaticamente em bloco delimitado de dados não confiáveis).
- `{last_output}`: saída do passo imediatamente anterior (encapsulada automaticamente em bloco delimitado de dados não confiáveis).
- `{instruction}`: conteúdo do próprio campo `instruction`.
- `{<key_de_passo_anterior>}`: saída de qualquer passo anterior (ex.: `{plan}`, `{code}`), também encapsulada automaticamente em bloco delimitado.

Por padrão, o Council envolve saídas de agentes nos placeholders acima com delimitadores:

- `===DADOS_DO_AGENTE_ANTERIOR===`
- `===FIM_DADOS_DO_AGENTE_ANTERIOR===`

Esse encapsulamento reduz risco de prompt injection indireto entre etapas ao separar instrução do passo de dados vindos de LLMs anteriores.

## 5.1 Placeholder no `command` e Autocompletar Gemini

Algumas CLIs não leem bem por `stdin`. Para esses casos, você pode usar o placeholder `{input}` no campo `command`:

```json
{
  "command": "gemini -p {input}"
}
```

Quando `{input}` está presente, o Council injeta o prompt já escapado no próprio comando e não envia conteúdo via `stdin` para esse passo.
Nessa rota, o payload é delimitado automaticamente com:

- `===COUNCIL_INPUT_ARGV_START===`
- `===COUNCIL_INPUT_ARGV_END===`

**Especial para Gemini**: Como conveniência extra, se você configurar o comando estritamente como `gemini -p` ou `gemini --prompt` (sem indicar o valor e sem usar explicitamente o placeholder `{input}`), o executor irá detectar esse padrão automaticamente. Ele tratará a sintaxe anexando o payload escapado no final do comando de forma invisível.

## 6. Regras de Validação

O carregamento falha com erro claro quando:

1. O arquivo JSON não existe.
2. O JSON é inválido.
3. `steps` está ausente (quando objeto) ou formato não é lista.
4. Não há nenhum passo.
5. Campos obrigatórios não existem ou não são string.
6. `is_code` não é boolean.
7. Há `key` duplicada.
8. A `key` usa nome reservado: `user_prompt`, `full_context`, `last_output`, `instruction`.
9. O `input_template` referencia placeholder inexistente.
10. O `command` usa binário inexistente no `PATH`.
11. O `command` usa binário fora da allowlist (`claude`, `gemini`, `codex`, `ollama`).
12. O `command` usa caminho explícito no primeiro token (ex.: `/usr/bin/codex`).
13. O `command` contém quebras de linha (`\n`, `\r`) ou operadores de shell não permitidos: `|`, `&&`, `;`, `` ` ``, `$(`, `>`, `>>`.
14. `timeout`, `max_input_chars`, `max_output_chars` ou `max_context_chars` não são inteiros positivos.
15. `COUNCIL_REQUIRE_FLOW_SIGNATURE` está ativo e o arquivo não possui assinatura válida/confiada.

## 6.1 Limites Globais por Ambiente

Além dos limites por passo no JSON, o Council permite defaults globais via ambiente:

- `COUNCIL_MAX_CONTEXT_CHARS`
- `COUNCIL_MAX_INPUT_CHARS`
- `COUNCIL_MAX_OUTPUT_CHARS`

Se qualquer uma dessas variáveis estiver definida com valor inválido (não numérico ou `<= 0`), a execução falha na inicialização com erro explícito.

## 7. Exemplo Completo

```json
{
  "steps": [
    {
      "key": "plan",
      "agent_name": "Claude",
      "role_desc": "Planejamento",
      "command": "claude -p",
      "instruction": "Você é um arquiteto. Crie um plano.",
      "input_template": "{instruction}\n\n{user_prompt}",
      "style": "dark_goldenrod"
    },
    {
      "key": "critique",
      "agent_name": "Gemini",
      "role_desc": "Crítica",
      "command": "gemini -p {input}",
      "instruction": "Critique o plano com foco técnico e segurança.",
      "input_template": "{instruction}\n\nPlano:\n{plan}",
      "style": "dodger_blue1"
    },
    {
      "key": "implement",
      "agent_name": "Codex",
      "role_desc": "Implementação",
      "command": "codex exec --skip-git-repo-check",
      "instruction": "Implemente com base no plano e na crítica. Retorne só código.",
      "input_template": "{instruction}\n\nPlano:\n{plan}\n\nCrítica:\n{critique}",
      "is_code": true,
      "style": "bright_black"
    },
    {
      "key": "review",
      "agent_name": "Claude",
      "role_desc": "Revisão Final",
      "command": "claude -p",
      "instruction": "Faça um review do código e priorize melhorias.",
      "input_template": "{instruction}\n\nCódigo:\n{implement}",
      "style": "dark_goldenrod"
    }
  ]
}
```

## 8. Dica Operacional

Para validar seu fluxo sem depender de provedores externos, use temporariamente um passo com `command: "cat"` e verifique se os templates estão sendo montados corretamente.
