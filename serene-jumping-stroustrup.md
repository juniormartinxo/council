# Plano: Parser Estrito de Segurança no método `_step`

## Contexto

No método `_step` de `council/orchestrator.py`, existe uma falha de segurança: a variável `result` bruta (saída direta do LLM, potencialmente com alucinações ou injeções) é salva em `self.state.add_turn()` e retornada ao pipeline **antes** de qualquer sanitização. A limpeza dos blocos Markdown só ocorre na variável `result_display`, usada apenas para exibição na UI.

O objetivo é implementar um parser estrito com semântica **fail-close**: se `is_code=True` e o LLM não retornar um bloco Markdown válido, a execução para imediatamente com um `CommandError`.

---

## Arquivo a Modificar

- `/home/junior/apps/jm/council/council/orchestrator.py` — método `_step` (linhas ~187–301)

## Imports já disponíveis (sem alterações necessárias)

```python
import re  # já importado na linha 1
from council.executor import Executor, CommandError, ExecutionAborted  # já importado
```

---

## Implementação

### Bloco atual (dentro do `try`, após `run_cli`) — linhas ~235–255

```python
result = self.executor.run_cli(...)

self.state.add_turn(agent_name, "assistant", result, role_desc)  # ← resultado bruto

result_display = result
if is_code:
    lines = result.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    result_display = "\n".join(lines).strip()

self.ui.show_panel(..., result_display, ...)
self._successful_steps += 1
return result  # ← resultado bruto retornado
```

### Bloco novo (substituição)

```python
result = self.executor.run_cli(
    command,
    input_data,
    timeout=timeout,
    on_output=update_cb,
    max_input_chars=max_input_chars,
    max_output_chars=max_output_chars,
)

# Parser estrito de segurança (Regras 1, 2 e 3)
if is_code:
    match = re.search(r'```[^\n]*\n([\s\S]*?)```', result)
    if not match:
        raise CommandError(
            "Bloqueio de Segurança: A saída do agente não contém um bloco Markdown válido."
        )
    result = match.group(1).strip()

# Apenas conteúdo validado e sanitizado chega ao estado e ao retorno
self.state.add_turn(agent_name, "assistant", result, role_desc)
self.ui.show_panel(f"{agent_name} - {role_desc}", result, style=style, is_code=is_code)
self._successful_steps += 1
return result
```

---

## Detalhes das Regras de Negócio

| Regra | Descrição | Implementação |
|-------|-----------|---------------|
| 1 — Validação via Regex | Extrai conteúdo dentro de ```` ```..``` ```` usando `re.search` com `[\s\S]*?` | `re.search(r'```[^\n]*\n([\s\S]*?)```', result)` |
| 2 — Fail-Close | Se `is_code=True` e sem match, lança `CommandError` imediatamente | `raise CommandError(...)` antes de `add_turn` |
| 3 — Sanitização Real | `result` é sobrescrito com o conteúdo limpo antes de `add_turn` e `return` | `result = match.group(1).strip()` |

### Notas sobre a regex
- ```` ```[^\n]* ```` — captura a linha de abertura com identificador de linguagem opcional (ex: `python`, `bash`)
- `\n` — consome a quebra de linha após o delimitador de abertura
- `([\s\S]*?)` — captura o conteúdo interno (grupo 1), incluindo quebras de linha, de forma não-gananciosa
- ```` ``` ```` — fecha no primeiro delimitador de fechamento encontrado
- `re.search` (vs `re.match`) — localiza o bloco em qualquer posição do texto, tolerando preâmbulo de texto antes do bloco

### Simplificação da variável `result_display`
A variável `result_display` se torna desnecessária: quando `is_code=True`, `result` já é o conteúdo limpo; quando `is_code=False`, não há transformação. O `show_panel` passa a receber `result` diretamente.

---

## Verificação

1. **Teste manual — caso feliz**: Executar o fluxo com um agente que retorna bloco Markdown válido; confirmar que `state.history` contém apenas o conteúdo interno (sem os delimitadores ```` ``` ````).
2. **Teste manual — fail-close**: Modificar temporariamente o comando para retornar texto plano sem bloco Markdown quando `is_code=True`; confirmar que o fluxo para com `CommandError: Bloqueio de Segurança...`.
3. **Teste com `is_code=False`**: Confirmar que fluxos sem flag de código continuam funcionando normalmente (sem regressão).
4. **Testes unitários existentes**: Rodar `pytest tests/` para garantir que não há regressão em `test_executor.py` e demais suítes.
