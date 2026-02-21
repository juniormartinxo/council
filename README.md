# Council - MAS CLI Orquestrador

Council é um sistema multiagente (MAS) via CLI construído em Python que orquestra a execução de três LLMs locais (`claude-cli`, `gemini-cli`, `codex-cli`).
Ele cria um loop de consenso, enviando as saídas de um modelo como entrada (contexto) para o próximo, automatizando um pipeline de Arquitetura, Crítica, Código Final e Code Review.

## Estrutura de Diretórios Recomendada

```text
council/
├── council/
│   ├── __init__.py       # Módulo Python principal
│   ├── main.py           # Entrypoint da aplicação e roteamento do Typer 
│   ├── ui.py             # Interface no terminal rica com Rich (Spinners, Panels, Syntax)
│   ├── executor.py       # Wrapper do subprocess para comunicar de forma segura com os CLIs
│   ├── state.py          # Classe/módulo de gerenciamento de Estado e Memória 
│   └── orchestrator.py   # Lógica central do fluxo de repasse de contexto
├── requirements.txt      # Dependências Python (Typer, Rich, etc)
└── README.md             # Esta documentação
```

## Como Instalar

```bash
# 1. Crie um ambiente virtual (opcional, mas recomendado)
python -m venv venv
source venv/bin/activate

# 2. Instale os requisitos
pip install -r requirements.txt
```

> **Nota:** Certifique-se de que as ferramentas CLI fictícias (`claude-cli`, `gemini-cli` e `codex-cli`) estejam no seu `$PATH` e sejam capazes de aceitar entrada via "stdin".

## Como Executar

O comando principal, via Typer, exige que você rode a aplicação pelo `main.py` passando o comando `run` e um prompt.

```bash
# Formato: python -m council.main run "<prompt>"
python -m council.main run "criar uma api de login"
```

O fluxo gerado será:

1. **Planejamento:** Claude-cli gera a arquitetura base
2. **Crítica:** Gemini-cli verifica a arquitetura proposta
3. **Consolidação:** Claude-cli acata as críticas e refina a proposta
4. **Implementação:** Codex-cli gera o código final baseado no refinamento 
5. **Revisão Final:** Gemini-cli audita o código gerado em busca de vulnerabilidades e boas práticas.
