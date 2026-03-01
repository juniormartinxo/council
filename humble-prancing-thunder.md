# Plano: Campo `model` no flow.json

## Contexto

Atualmente, o modelo LLM usado em cada step é definido implicitamente pelo campo `command`
(ex: `"claude -p"`, `"gemini -p {input}"`). Para trocar o modelo, o usuário precisa conhecer
os flags exatos de cada CLI (ex: `claude --model claude-opus-4-5 -p`), o que é pouco ergonômico.

O objetivo é permitir que o usuário especifique o modelo diretamente via campo `model` no
flow.json, de forma separada e legível:

```json
{
  "command": "claude -p",
  "model": "claude-opus-4-5"
}
```

Este plano incorpora as correções apontadas pelo Codex: remoção do call duplicado, detecção
de conflito robusta (`--model=foo`), validação de `model` vazio e com espaços internos, e
atualização da seção DeepSeek nos docs.

## Abordagem

Adicionar campo `model` opcional em `_parse_step` (`config.py`). Se presente, o valor é
injetado como `--model <model>` imediatamente após o binário no `command`. O `FlowStep`
resultante tem o `command` já com o modelo embutido — sem nenhuma mudança no executor ou
orchestrator.

**Limitação conhecida**: Editar via TUI (`flow edit`) serializa apenas o campo `command`
do `FlowStep`. Após uma edição via TUI, o `command` terá `--model` embutido, mas o campo
`model` separado não será preservado — é uma limitação de UX, não um bug de corretude.
Documentar isso nos docs.

## Arquivos Críticos

- `council/config.py` — único arquivo de código a modificar (linhas 332–368, 415–447)
- `docs/FLOW_CONFIG.md` — documentar o novo campo (linhas 157–173) e atualizar seção DeepSeek (linha 235)
- `flow.example.json` — adicionar `"model"` em pelo menos um step de exemplo
- `tests/test_config.py` — adicionar casos de teste usando padrões existentes

## Implementação

### 1. `council/config.py`

**a) Adicionar constante** logo após `API_ONLY_COMMAND_BINARIES` (linha ~42):
```python
MODEL_SUPPORTING_BINARIES = frozenset({"claude", "gemini", "deepseek"})
```

**b) Adicionar `import re` no topo** se ainda não presente (já existe via `DISALLOWED_COMMAND_PATTERNS`).

**c) Nova função `_inject_model_into_command`** (adicionar após `_validate_command`):
```python
def _inject_model_into_command(command: str, model: str, step: int) -> str:
    tokens = shlex.split(command)
    binary = tokens[0]
    if binary not in MODEL_SUPPORTING_BINARIES:
        raise ConfigError(
            f"O campo 'model' no passo #{step} não é suportado para o binário '{binary}'. "
            f"Binários suportados: {', '.join(sorted(MODEL_SUPPORTING_BINARIES))}."
        )
    # Detecta --model ou -m em qualquer forma: "--model", "--model=foo", "-m", "-m=foo"
    conflict = any(
        t == "--model" or t == "-m"
        or t.startswith("--model=") or t.startswith("-m=")
        for t in tokens
    )
    if conflict:
        raise ConfigError(
            f"O campo 'model' no passo #{step} conflita com --model já presente em 'command'. "
            "Use apenas um dos dois."
        )
    return shlex.join([tokens[0], "--model", model, *tokens[1:]])
```

**d) Em `_parse_step`**, após a chamada de `_validate_command` (linha 340), inserir:
```python
model = _get_string(raw_step, ["model"], required=False)
if model is not None:
    if not model:
        raise ConfigError(f"O campo 'model' no passo #{position} não pode ser vazio.")
    if " " in model or "\t" in model or "\n" in model or "\r" in model:
        raise ConfigError(
            f"O campo 'model' no passo #{position} não pode conter espaços ou quebras de linha."
        )
    command = _inject_model_into_command(command, model, step=position)
```

> **Nota**: `_get_string` com `required=False` retorna `None` (ausente) ou `str` após `.strip()`.
> A string pode ser `""` se o valor JSON for `"   "` — por isso validamos `if not model:`.

### 2. `docs/FLOW_CONFIG.md`

**Na seção "4. Campos de Cada Passo"** (linha ~162), adicionar após a descrição de `command`:
```
- `model` (opcional): modelo LLM a usar (ex: `claude-opus-4-5`, `gemini-2.5-pro`).
  Injeta `--model <model>` no comando, logo após o binário. Suportado apenas para
  `claude`, `gemini` e `deepseek`. Incompatível com `--model` (ou `-m`) já presente
  em `command`. **Atenção**: editar o step via `flow edit` não preserva o campo `model`
  separado — o `command` resultante terá `--model` embutido.
```

**Na seção DeepSeek** (linha 235), harmonizar com o novo campo:
```
- Use `command` como `deepseek` e campo `model` como `deepseek-chat` (default) ou
  `deepseek-reasoner`. Alternativamente, use flags diretamente no `command`:
  `deepseek --model deepseek-chat`.
```

### 3. `flow.example.json`

Adicionar `"model"` no primeiro step (Claude/plan) como demonstração:
```json
{
  "key": "plan",
  "agent_name": "Claude",
  "role_desc": "Planejamento",
  "command": "claude -p",
  "model": "claude-sonnet-4-6",
  ...
}
```

### 4. `tests/test_config.py`

Usar o padrão existente (`_step_payload`, `mock_command_lookup`, `isolated_config_env`).

Novos casos de teste:
1. `test_model_field_injects_into_claude_command` — `"claude -p"` + `"model": "claude-opus-4-5"` → `command` começa com `claude --model claude-opus-4-5 -p`
2. `test_model_field_injects_into_gemini_command` — `"gemini -p {input}"` + `"model": "gemini-2.5-pro"` → injeção correta
3. `test_model_field_injects_into_deepseek_command` — `"deepseek"` + `"model": "deepseek-reasoner"` → injeção correta
4. `test_model_field_raises_for_unsupported_binary` — `"codex exec"` + `"model": "gpt-4"` → `ConfigError` mencionando `codex`
5. `test_model_field_raises_when_model_in_command_long_flag` — `"claude --model foo -p"` + `"model": "bar"` → `ConfigError`
6. `test_model_field_raises_when_model_in_command_equals_form` — `"claude --model=foo -p"` + `"model": "bar"` → `ConfigError`
7. `test_model_field_raises_when_model_is_empty_string` — `"model": ""` → `ConfigError`
8. `test_model_field_raises_when_model_has_spaces` — `"model": "claude opus"` → `ConfigError`
9. `test_model_field_is_optional` — step sem campo `model` continua funcionando normalmente

## Verificação

1. **Testes unitários**: `pytest tests/test_config.py -v` — todos os 9 novos casos devem passar
2. **Teste manual**: Criar flow.json com `"model": "claude-opus-4-5"` em um step, rodar
   `council run "teste rápido"` e verificar nos logs de auditoria que o command executado
   contém `--model claude-opus-4-5`
3. **Teste de rejeição**: Colocar `"model": "x"` em um step com `"command": "codex exec"` e
   verificar que o carregamento do flow falha com mensagem clara sobre binário não suportado
4. **Teste de conflito**: Usar `"command": "claude --model=foo -p"` + `"model": "bar"` e
   verificar mensagem de conflito clara
