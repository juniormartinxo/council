# Roadmap de Produto: Council (Terminal-First)

A essência e o grande diferencial do **Council** é ser uma ferramenta "Direct-to-Developer" focada no isolamento que apenas o terminal e chamadas de Unix/Pipes proporcionam.

Para transformá-lo em um produto premium para desenvolvedores (uma "DevTool" distribuível via `brew`, `pipx`, `npm` ou binário standalone), o objetivo é elevar a experiência do terminal ao máximo, mantendo-o **100% no Terminal e CLI**.

Abaixo estão os pilares de evolução organizados por prioridade, com indicação do que já foi implementado.

---

## 0. Fundação Técnica (Pré-requisitos de Produto)

Antes de avançar em features de alto impacto, a base técnica precisa sustentar o ritmo de evolução.

### ✅ Já implementado

*   **Empacotamento (`pyproject.toml`):** O Council é distribuível como pacote Python com entry-point `council` via `pip install .` ou `pipx install .`. Comando global `council run` e `council tui` funcionam sem `python -m`.
*   **Diretório de dados do usuário (`COUNCIL_HOME`):** Módulo `paths.py` centraliza caminhos de armazenamento respeitando `XDG_CONFIG_HOME` (Linux), `~/Library/Application Support` (macOS) e `APPDATA` (Windows). O estado da TUI já persiste em `~/.config/council/tui_state.json`.
*   **Resolução de configuração em cascata:** O `flow.json` é resolvido automaticamente em 4 níveis: `--flow-config` → `$COUNCIL_FLOW_CONFIG` → `./flow.json` (CWD) → `~/.config/council/flow.json` → default interno.
*   **Testes automatizados (suite mínima `pytest`):** Base de testes criada em `tests/` com cobertura de smoke tests para `config.py` (parsing de JSON, validação de duplicatas/chaves reservadas, templates e hardening de `command` com `which()` + bloqueio de operadores) e `executor.py` (preparação de comandos, placeholder `{input}`, variações de prompt do Gemini, sucesso/erro/timeout/cancelamento em `run_cli`). `pyproject.toml` atualizado com `project.optional-dependencies.dev` e configuração de `pytest`.

### 🔜 Próximos passos

*   **CI de Testes:** Executar `pytest` automaticamente em pull requests e merges para proteger regressões do core (`config`, `executor`, `orchestrator` e TUI state) e tornar a validação contínua, não apenas local.
*   **Persistência estruturada (`COUNCIL_HOME/db`):** `CouncilState` é 100% in-memory (`list[Turn]` que nasce e morre com o processo). Introduzir um banco SQLite local para historiar runs completos (prompt, steps executados, outputs, duração, timestamps). Esse banco é pré-requisito direto dos pilares de Telemetria (§4) e Grafos (§1).

---

## 1. Sistema Dinâmico de Grafos e Loops Condicionais (Orquestração Avançada)

Atualmente o `Orchestrator.run_flow()` é um loop `for` sequencial sobre `self.flow_steps`. O `FlowStep` é um `dataclass(frozen=True)` sem campos para condições, branches ou referências a outros steps. Em um produto maduro, o desenvolvedor precisa lidar com falhas arquiteturais ou testes que não passam diretamente na CLI.

### 🔜 Próximos passos

*   **Condicionais e Desvios:** Adicionar campos opcionais ao `FlowStep` para controle de fluxo declarativo:
    ```json
    {
      "key": "implement",
      "on_failure": { "goto": "implement", "max_retries": 3 },
      "validator": { "command": "pytest", "success_exit_code": 0 }
    }
    ```
    Evitar DSLs baseadas em strings livres (`"if error in {review} goto {implement}"`) que são frágeis de parsear e difíceis de debuggar. O formato declarativo mantém a coerência com o sistema de configuração JSON já existente e é validável pelo `config.py`.

*   **Aprovação Automatizada (Auto-Evaluate):** Introduzir a figura de um executor de scripts/verificador no pipeline. Exemplo: um agente gera código Python, um passo intermediário executa `pytest`, e se falhar (`exit code != 0`), o output volta para o agente consertar o código automaticamente até passar ou atingir o limite de retentativas. O mecanismo de `_collect_human_feedback_loop` no Orchestrator já implementa o padrão de retry com feedback — o Auto-Evaluate seria a mesma lógica trocando o checkpoint humano por um script validador.

*   **Refatoração do Orchestrator:** O `run_flow` precisa migrar de um `for step in self.flow_steps` linear para uma **máquina de estados** baseada em cursor, capaz de pular para steps anteriores, repetir steps, ou bifurcar a execução.

---

## 2. Resiliência do Executor (Rate Limits e Retry)

O `Executor.run_cli()` trata qualquer `exit code != 0` como erro fatal, abortando o fluxo com `CommandError`. Não existe diferenciação entre erros transitórios (rate limit `429`, timeout de rede) e erros permanentes (modelo não encontrado, auth inválida).

### 🔜 Próximos passos

*   **Backoff Exponencial:** Decorator de retry no `run_cli` para erros transitórios. Parsear `stderr` para padrões conhecidos (`429 Too Many Requests`, `rate limit`, `quota exceeded`) e reaplicar com backoff crescente antes de abortar. Número máximo de retentativas configurável.
*   **Classificação de Erros:** Diferenciar no `stderr` entre erros de auth (ação: pedir reconfiguração), rate limit (ação: retry), modelo não encontrado (ação: abort com mensagem clara) e erros genéricos (ação: abort com log).

---

## 3. Experiência e Instalação (Distribuição Independente)

Um produto precisa ser instalável de forma universal sem fricção, evitando dores de cabeça com ambientes virtuais Python ou gerenciamento de bibliotecas por fora.

### ✅ Já implementado

*   **Pacote Python distribuível:** `pyproject.toml` com `setuptools`, entry-point `council = "council.main:cli"`, dependências declaradas. Instalável via `pipx install .`.
*   **Documentação de instalação global:** README e CONTRIBUTING atualizados com instruções de `pipx install .`.

### 🔜 Próximos passos

*   **Binário Autocontido:** Compilar o Council usando PyInstaller, Nuitka, ou empacotador similar para gerar binários únicos (`council-linux-x64`, `council-macos-arm64`). O cliente faria apenas: `curl -fsSL https://council.dev/install | bash`.
*   **Publicação no PyPI:** Permitir `pipx install council-mas` sem clonar o repositório. Exige CI/CD com versionamento semântico automatizado.

### 🔮 Futuro

*   **Gerenciador de Dependências de Modelos Embutido:** Hoje o Council depende da injeção no PATH via CLIs externas (`claude`, `gemini`, `codex`). Introduzir abstração de *Adapters* opcionais. Exemplo: `council auth anthropic --key xyz`, permitindo que o Council faça requests HTTP diretamente quando a CLI global não for encontrada. **Atenção:** isso deve ser um *modo alternativo*, nunca substituição do modelo atual de CLIs externas, para preservar a filosofia agnóstica que é o DNA do projeto.

---

## 4. Telemetria CLI e Monitoramento de Custos

Agentes gastam tokens e ciclos de planejamento demoram. Rodar pipelines longos em APIs externas requer clareza nos gastos.

> **Dependência:** Este pilar depende da **persistência estruturada** (§0) para ter onde armazenar os dados coletados.

### 🔜 Próximos passos

*   **Coleta de métricas por step:** Registrar no banco local o tempo de execução de cada passo, exit codes, tamanho do input/output em caracteres, e estimativas de tokens consumidos quando extraíveis.
*   **Dashboard TUI de Analítica:** Adicionar uma view dedicada (`council metrics` ou atalho `Ctrl+M` na TUI) exibindo resumo de uso por sessão/período. Dados agregados do BD local para histórico descritivo.

---

## 5. Edição in-place e Integração com Editores via CLI

O Council exibe o output nativamente na TUI textualmente (ou guarda em clipboard). O processo final de dev passa por consolidar e aplicar os resultados em seus artefatos fonte.

### 🔜 Próximos passos

*   **Aplicação Direta de Patch (Diffing/Merge):** Incluir suporte a saídas no formato "Patch" (diff unix). Ao detectar um diff validado na última etapa, a TUI renderiza a intenção visualmente na tela (verde/vermelho) e apresenta a decisão via checkpoint interativo: `[Y]es to apply patch, [N]o, [A]djust?`. O mecanismo de `_collect_human_feedback_loop` já presente no Orchestrator pode ser estendido para suportar este tipo de ação além de "Continuar" e "Enviar ajuste".
*   **Abertura do Editor (`$EDITOR`):** Um atalho na TUI (`Ctrl+E`) para injetar instantaneamente o buffer da resposta da etapa ativa no Neovim, VSCode ou editor default do ambiente do desenvolvedor.

---

## 6. Ambientes de Sandboxing Seguros (Ferramentas no Terminal)

Se os agentes interagirem entre si e precisarem listar diretórios, criar arquivos massivos ou testar comandos do sistema fora da aprovação da TUI, deixá-los atuar diretamente sobre o host do usuário é um grande risco de segurança e arquitetura. Hoje o `Executor` roda `subprocess.Popen` com `shell=True` diretamente no host.

> **Dependência:** Este pilar ganha urgência assim que o sistema de Grafos (§1) permitir execução automática de validadores, pois agentes passariam a executar código sem aprovação humana.

### 🔮 Futuro

*   **Working directory isolado (fase leve):** Antes do Docker completo, executar agentes de implementação em um `tempdir` isolado e devolver apenas os diffs resultantes. Custo mínimo de implementação e já mitiga danos acidentais ao filesystem do host.
*   **Integração nativa com Docker (fase completa):** Feature `council run --isolated`, onde o pipeline criaria um container efêmero invisível, injetaria os binários e STDINs ali dentro, e só devolveria os resultados (`code`, `diffs`) finais validados. O usuário aprovaria apenas o *merge* das alterações no host. O `Executor` está bem isolado arquiteturalmente, permitindo um `DockerExecutor(Executor)` ou wrapper no `run_cli` sem impacto no core.

---

## 7. Templates e Comunidade de Fluxos

> **Dependências:** Este pilar depende do **Sandboxing** (§6) como pré-requisito de segurança, pois fluxos de terceiros contêm campos `command` que executam comandos arbitrários no sistema.

### 🔮 Futuro

*   **Biblioteca de Presets embutida:** Diretório `examples/` com fluxos pré-configurados para cenários comuns (security audit, refactoring, test generation). Acessíveis via flag `--flow-preset sec-audit` sem necessidade de registry externo.
*   **Comunidade de Fluxos (`flow.json`):** Quando houver base de usuários e sandboxing ativo, facilitar a importação de topologias avançadas criadas pela comunidade através de um registry focado na linha de comando.
    *   *Ex:* `council flow install auto-code-refactor`
    *   *Ex:* `council flow install sec-audit-pipeline`

---

## Resumo de Priorização

| Prioridade | Pilar | Justificativa |
| :--- | :--- | :--- |
| **P0** | §0 Fundação (CI de testes + persistência) | Pré-requisito técnico para tudo que vem depois |
| **P1** | §1 Grafos e Loops Condicionais | Maior impacto funcional — transforma o pipeline de linear em inteligente |
| **P1** | §2 Resiliência do Executor | Bug-fix disfarçado de feature — rate limits abortam pipelines silenciosamente |
| **P2** | §3 Distribuição (binário + PyPI) | Base já existe, falta o "último mile" para adoção ampla |
| **P2** | §5 Edição in-place / `$EDITOR` | Fecha o ciclo do dev — o Council gera código e ele é aplicado diretamente |
| **P3** | §4 Telemetria | Valor cresce com uso recorrente; depende de persistência |
| **P3** | §6 Sandboxing | Urgência cresce com Auto-Evaluate (§1); sem ele, risco é mitigado pelo checkpoint humano |
| **P4** | §7 Templates / Marketplace | Depende de comunidade + sandboxing; fluxos de exemplo resolvem o curto prazo |

> Estas implementações preparam a migração do Council de um excelente orquestrador arquitetural de MAS, para um Asset Produtivo Indispensável (DevTool de Prateleira) completamente nativo no Terminal do programador moderno.
