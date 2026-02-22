<h1 align="center">Council 🏛️ <br/> <em>AI Multi-Agent Orquestrator (MAS)</em></h1>

<p align="center">
  <strong>Uma arquitetura robusta, assíncrona e orientada a eventos para orquestração de Modelos Fundacionais Locais e em Nuvem via CLI.</strong>
</p>

---

## 🚀 Sobre o Projeto

O **Council** é um orquestrador CLI construído do zero em **Python**, que projeta um consenso automatizado (Multi-Agent System) entre instâncias distintas de LLMs. Em vez de depender de pesadas bibliotecas de abstração de IA (como LangChain ou AutoGen), o Council adota uma abordagem de infraestrutura *agnóstica*, conectando-se diretamente a ferramentas bash e CLIs independentes (`claude`, `gemini`, `codex`) via injeção segura de `stdin/stdout`.

Este projeto é um laboratório prático de **Engenharia de Software e Arquitetura de Sistemas**, demonstrando forte domínio em gerenciamento de processos do Sistema Operacional, manipulação de streams de dados IO sem bloqueio, e desenvolvimento de interfaces ricas baseadas em terminal (TUI).

O fluxo de agentes é **configurável por arquivo JSON**, permitindo que cada time defina qual IA assume cada papel (planejamento, crítica, implementação, revisão etc.) sem editar o código-fonte.

Guia para contribuir com o projeto: `CONTRIBUTING.md`.

## 🧠 Soluções de Engenharia e Arquitetura

O desenvolvimento do Council focou-se na resiliência e na separação de responsabilidades (SoC), abordando os seguintes desafios técnicos complexos:

### 1. Manipulação Assíncrona de Subprocessos
Chamadas a LLMs são bloqueantes por natureza. Pípes padrão (como `subprocess.communicate()`) fariam o programa refém do tempo de geração do modelo, ofuscando e limitando o feedback visual do terminal.
* **A Solução:** Implementou-se uma leitura em tempo real (linha por linha, sem *buffer*) do descritor de arquivo `stdout` da ferramenta externa. Utilizando iteradores de leitura passados a callbacks injetados, construiu-se uma ponte limpa entre o modelo rodando no kernel (filho) e a interface do usuário (pai), permitindo exibição do `Live Stream` na tela milissegundos após o token ter sido retornado pelo LLM. 

### 2. Gerenciamento de Estado de Contexto Contínuo
Como CLIs são *stateless*, cada chamada a um agente esquece a iteração isolada do agente anterior.
* **A Solução:** Uma classe de domínio `CouncilState` gerencia a Memória da aplicação, encapsulando coleções de *Turns* e injetando na estrutura das prompts dinamicamente os cabeçalhos de papel e as respostas consolidadas de execuções anteriores no subshell, forçando a preservação do escopo do pipeline.

### 3. Integração em Ambiente Headless e Tratamento de TTY
CLIs complexos detectam a presença do shell `tty` nativo. Invocações programáticas causam falhas como "stdin is not a terminal" se manuseadas incorretamente, além de interrupções por agentes tentando pedir validação do humano na tela.
* **A Solução:** Engenharia reversa para envio de metadados invisíveis/parametrizadores (ex: flags `-p`, ou subcomandos headless como `exec`) isolando e castrando os módulos gráficos ou de aprovação (*Yolo Mode* programático), forçando os *clients* a interpretarem o programa Python em canais canônicos absolutos de texto limpo. Fechamento proativo de buffers (`stdin.close()`) forçando envio EOF para evitar pipelines corrompidos (Dangling processes).

### 4. Event-Driven UI com Context Managers Modernos
A biblioteca `rich` e o `typer` compõem a porta de entrada.
* **A Solução:** Emprego extensivo de `@contextmanager` para isolar fluxos UI. Um Painel Dinâmico é capaz de renderizar as últimas `N` linhas emitidas de um LLM como tela de log e se auto-destruir de forma limpa (`transient=True`), sendo trocado perfeitamente pelo Syntax Highlighter de `markdown` para a versão imutável do log validado. Tudo através de injeção de dependência rudimentar do injetor raiz (`Orchestrator(state, executor, ui)`).

---

## 🛠️ Stack Tecnológica

| Tecnologia | Função no Projeto |
| :--- | :--- |
| **Python 3.10+** | Core languange focado em Type Hinting modernos (`typing_extensions.Annotated`). |
| **Typer** | Roteamento nativo e performático de argumentos via Python types. |
| **Rich** | Controle de Buffer de frame do Terminal (Painéis, Syntax Highlighting, Spinners, Live Updates). |
| **Textual** | Interface TUI interativa para executar o mesmo fluxo multimodelo com painel de stream e resultados. |
| **Subprocess** | Integração em baixo nível de Pipes SO Popen (`stdin`, `stdout`, `stderr`). |
| **OOP / SOLID** | Padrões de classes dedicados a Responsabilidade Única (UI, Estado, Execução Pura). |

## 🧬 Dissecando o Loop de Consenso

Por padrão, o Orchestrator executa a seguinte topologia seqüencial em pipeline para processamento da entrada:

1. `Claude` **[Arquitetura]**: Planeja os diagramas lógicos a partir do input primitivo.
2. `Gemini` **[Critique]**: Audita as fragilidades, segurança falha e complexidades excessivas (Big-O).
3. `Claude` **[Consolidation]**: Refatora as fraquezas sistêmicas do design original.
4. `Codex` **[Engineer]**: Converte a macro visão consolidada em código-fonte direto ao ponto.
5. `Gemini` **[Reviewer]**: Inspeciona falhas sintáticas ou de coesão, fechando o loop. 

Se necessário, esse pipeline pode ser sobrescrito via `--flow-config` ou `COUNCIL_FLOW_CONFIG`.

## ⚙️ Configurando Papéis e IAs

O passo a passo completo da feature está em `docs/FLOW_CONFIG.md`.
Visão de execução ponta-a-ponta com diagrama Mermaid: `docs/APPLICATION_FLOW.md`.

Regras de seguranca aplicadas ao `flow.json` (campo `command`):

- O binario (primeiro token) precisa existir no `PATH`.
- O binario precisa estar na allowlist: `claude`, `gemini`, `codex`, `ollama`.
- O primeiro token deve ser apenas nome de binario (caminho explicito como `/usr/bin/codex` e bloqueado).
- O parser rejeita `\n`/`\r` e operadores de shell perigosos (`|`, `&&`, `;`, `` ` ``, `$(`, `>`, `>>`).
- Fluxos de origem nao confiavel ainda exigem cautela, pois comandos allowlisted continuam executando no host local.

Resumo rápido:

1. Crie seu fluxo a partir do exemplo:

```bash
cp flow.example.json flow.meu.json
```

2. Ajuste o mapeamento de papéis para as IAs no JSON.

3. Execute com configuração customizada:

```bash
council run "Seu prompt" --flow-config flow.meu.json
```

4. Ou defina globalmente por ambiente:

```bash
export COUNCIL_FLOW_CONFIG=flow.meu.json
council run "Seu prompt"
```

## ⚙️ Instalação Local

O ambiente não exige o uso do LangChain. Tudo se resume a ferramentas CLI padronizadas que existam no PATH do repositório/SO rodando de fato a mágica.

```bash
# Geração do ambiente virtual restrito e ativado
python3 -m venv venv
source venv/bin/activate

# Instalação das dependências (Rich, Typer, Textual etc)
pip install -r requirements.txt
pip install -e .

# Dispara a orquestração enviando o STDIN global para os sub-nós
council run "Crie um algoritmo distribuido de map-reduce"

# Dispara com fluxo customizado (escolhendo IAs/papéis livremente)
council run "Crie um algoritmo distribuido de map-reduce" --flow-config flow.example.json

# Abre a TUI interativa (Textual)
council tui

# Diagnostico explicito dos binarios exigidos pelo fluxo
council doctor
```

Na TUI, cada etapa possui checkpoint humano: você pode continuar, enviar ajuste para o mesmo agente (reexecução) ou abortar o fluxo.
Detalhes completos de uso da TUI, atalhos, abas por etapa, persistência e cópia estão em `docs/OPERATIONS.md`.

### Privacidade do histórico da TUI

- O estado da TUI fica em `~/.config/council/tui_state.json` (ou equivalente via `COUNCIL_HOME`).
- Esse arquivo armazena `last_flow_config` e pode armazenar histórico de prompts (`last_prompt` e `prompt_history`).
- Para limpar dados sensíveis explicitamente:

```bash
council history clear
```

- Para habilitar criptografia at-rest do histórico de prompts, defina uma senha no ambiente:

```bash
pip install -e ".[security]"
export COUNCIL_TUI_STATE_PASSPHRASE="sua-senha-forte"
```

- Quando `COUNCIL_TUI_STATE_PASSPHRASE` estiver definido, prompts não são persistidos em texto plano.
- Para reduzir exposição da senha em ambientes sensíveis, use arquivo de segredo:

```bash
printf '%s' 'sua-senha-forte' > ~/.config/council/passphrase.txt
chmod 600 ~/.config/council/passphrase.txt
export COUNCIL_TUI_STATE_PASSPHRASE_FILE=~/.config/council/passphrase.txt
```

### Persistência estruturada de runs

- O Council persiste execuções completas em `COUNCIL_HOME/db/history.sqlite3` (prompt, steps, outputs, duração e timestamps).
- O arquivo do banco é endurecido com permissão `0o600` e o diretório `COUNCIL_HOME/db` com `0o700` quando suportado pelo host.
- Para inspecionar rapidamente os últimos runs:

```bash
council history runs --limit 20
```

### Log de auditoria

- O Council registra eventos de execução em `COUNCIL_HOME/council.log` com timestamp, nível e payload estruturado.
- O arquivo de log usa permissão `0o600` e `COUNCIL_HOME` é endurecido para `0o700` quando suportado pelo host.
- O nível mínimo de log é configurável por `COUNCIL_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Valor inválido falha explicitamente.
- Rotação por tamanho disponível via `COUNCIL_LOG_MAX_BYTES` (default `5242880`) e `COUNCIL_LOG_BACKUP_COUNT` (default `5`).

## 📦 Instalação Global (recomendada)

Para usar o Council em qualquer diretório sem levar os arquivos do projeto, instale como aplicativo de linha de comando:

```bash
pipx install .
```

Depois disso, use:

```bash
council run "Seu prompt"
council tui
council doctor
```

`council run` e TUI fazem preflight automatico dos binarios do fluxo antes da orquestracao.

Resolução automática de fluxo quando `--flow-config` não for informado:

1. `COUNCIL_FLOW_CONFIG`
2. `./flow.json` (diretório atual)
3. `~/.config/council/flow.json` (ou equivalente no seu SO)
4. fluxo interno default

---
*Construído com base em design system limpo de código e arquitetura adaptável.*
