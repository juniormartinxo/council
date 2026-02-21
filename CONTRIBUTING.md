# Guia de Contribuicao

Obrigado por contribuir com o Council.

Este documento define um fluxo simples para contribuir sem quebrar o comportamento atual da CLI/TUI.

## 1. Antes de comecar

Objetivos aceitos para contribuicao:

- Correcao de bugs.
- Melhorias na TUI/CLI.
- Evolucao do fluxo configuravel (`flow_config`).
- Documentacao tecnica e operacional.

## 2. Setup local

Use Python 3.10+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Comandos principais do projeto:

```bash
python -m council.main run "Seu prompt"
python -m council.main run "Seu prompt" --flow-config flow.example.json
python -m council.main tui
```

## 3. Fluxo de desenvolvimento recomendado

1. Crie uma branch a partir da principal.
2. Faça mudancas pequenas e focadas em um unico objetivo.
3. Atualize a documentacao quando houver mudanca de comportamento.
4. Valide localmente antes de abrir PR.

## 4. Convencoes de codigo

- Mantenha a separacao de responsabilidades entre `orchestrator`, `executor`, `state` e `ui/tui`.
- Preserve compatibilidade de flags e comandos ja documentados.
- Use type hints ao adicionar novas funcoes.
- Evite acoplamento entre regra de negocio e detalhes de apresentacao.
- Evite alterar comportamento default sem atualizar `README.md`, `FLOW_CONFIG.md` ou `OPERATIONS.md`.

## 5. Validacao minima antes do PR

Execute pelo menos:

```bash
python -m compileall council
```

Se as CLIs externas estiverem disponiveis (`claude`, `gemini`, `codex`), rode tambem:

```bash
python -m council.main run "Smoke test local" --flow-config flow.example.json
python -m council.main tui
```

Se as CLIs externas nao estiverem disponiveis, descreva no PR que a validacao foi apenas estrutural (compilacao + revisao manual).

## 6. Checklist de PR

- O problema e a motivacao estao claros.
- As mudancas estao limitadas ao escopo proposto.
- A documentacao relevante foi atualizada.
- O impacto em fluxo default e fluxo customizado foi considerado.
- Para mudancas na TUI, inclua screenshot ou descricao objetiva do resultado visual.

## 7. Commits e revisao

- Prefira mensagens de commit diretas, no imperativo.
- Evite misturar refactor grande com mudanca funcional no mesmo commit sem justificativa.
- Responda feedback de revisao com a alteracao aplicada ou uma justificativa tecnica objetiva.

## 8. Reporte de bugs

Ao abrir issue, inclua:

- Passos para reproduzir.
- Resultado esperado.
- Resultado atual.
- Trecho de erro no terminal.
- Exemplo de `flow_config` (quando relevante).

## 9. Boas praticas de seguranca

- Nunca comite chaves, tokens ou credenciais.
- Nao inclua dados sensiveis em prompts de exemplo.
- Em mudancas de subprocesso, valide impacto em cancelamento, timeout e modo headless.
